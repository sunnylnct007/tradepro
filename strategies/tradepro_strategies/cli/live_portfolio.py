"""tradepro-live-portfolio — slow-loop runner.

Computes today's target portfolio for the trader's algo (sleeve +
ensemble + regime + vol target) and persists it as a strategy_runs +
strategy_decisions pair. Same data + same code as the equity_pipeline
backtest, just using the most-recent COMPLETED daily bar as the live
recommendation source instead of running over a multi-year window.

Cadence: typically run once per day after US close, OR triggered
manually pre-open. Idempotent — running twice in a row with the same
inputs produces a fresh row but identical decisions (same inputs_hash).

Output:
  - ~/.tradepro/cache/live_portfolio_latest.json   (local cache)
  - POST /api/ingest/live-portfolio                (with --push)

Read-side surfaces:
  - GET /api/live-portfolio/{strategy}/latest      (UI + MCP)
  - strategy_runs + strategy_decisions tables      (audit trail)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from ..cache import ensure_cached
from ..quant_engine import (
    QuantEngineConfig,
    build_high_beta,
    compute_live_portfolio,
)
from ..secrets import get_secret
from ..universes import wikipedia as wu

log = logging.getLogger("tradepro.cli.live_portfolio")

DEFAULT_OUT_PATH = Path.home() / ".tradepro" / "cache" / "live_portfolio_latest.json"


def _fetch_one(ticker: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    try:
        df = ensure_cached("yahoo", ticker, start, end, "1d")
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch %s failed: %s", ticker, exc)
        return None
    if df is None or df.empty:
        return None
    col_map = {c.lower(): c for c in df.columns}
    if not {"open", "high", "low", "close"}.issubset(col_map):
        return None
    return df.rename(columns={
        col_map["open"]: "Open",
        col_map["high"]: "High",
        col_map["low"]: "Low",
        col_map["close"]: "Close",
    })


def _fetch_many(tickers: list[str], start: datetime, end: datetime, *,
                 min_rows: int, label: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(tickers, 1):
        if i % 25 == 0:
            log.info("  %s: fetched %d/%d", label, i, len(tickers))
        df = _fetch_one(t, start, end)
        if df is not None and len(df) >= min_rows:
            out[t] = df
    log.info("%s: %d/%d tickers usable", label, len(out), len(tickers))
    return out


def _compute_hibeta_universe(
    cfg: QuantEngineConfig, spy_close: pd.Series,
    *, start: datetime, end: datetime,
) -> dict[str, pd.DataFrame]:
    """Build the high-beta sleeve via the trader's UniverseBuilder."""
    log.info("building high-beta sleeve (β>%.2f vs SPY, %dd lookback)",
             cfg.min_beta, cfg.beta_lookback)
    candidates: set[str] = set()
    for uname in ("sp500", "sp400_midcap"):
        try:
            for s in wu.fetch_universe(uname):
                candidates.add(s.ticker)
        except wu.UniverseFetchError as e:
            log.warning("%s scrape failed: %s", uname, e)
    candidates -= frozenset(cfg.crypto_exclude)
    sp_data = _fetch_many(
        sorted(candidates), start, end,
        min_rows=cfg.beta_lookback + 10, label="sp500+400",
    )
    closes = {t: df["Close"] for t, df in sp_data.items()}
    survivors = build_high_beta(
        spy_close, closes,
        min_beta=cfg.min_beta,
        beta_lookback=cfg.beta_lookback,
        crypto_exclude=cfg.crypto_exclude,
    )
    return {r.ticker: sp_data[r.ticker] for r in survivors}


def build_payload(
    *,
    strategy: str = "ichimoku_equity",
    cfg: QuantEngineConfig | None = None,
    include_large: bool = True,
    include_hibeta: bool = True,
    include_gold: bool = True,
) -> dict[str, Any]:
    """Run the slow loop end-to-end. Returns the JSON payload the
    /api/ingest/live-portfolio endpoint expects."""
    cfg = cfg or QuantEngineConfig()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # ~2 years of daily history is plenty for Ichimoku + 252d beta +
    # 60d vol + 200-SMA regime — cache fast-paths repeated runs.
    start = end - timedelta(days=730)

    log.info("fetching SPY benchmark...")
    spy_df = _fetch_one(cfg.benchmark, start, end)
    if spy_df is None:
        raise RuntimeError("SPY fetch returned empty — cannot derive regime")
    spy_close = spy_df["Close"]

    sleeve_data: dict[str, dict[str, pd.DataFrame]] = {}
    if include_large:
        log.info("fetching large_50 sleeve...")
        sleeve_data["equity_large"] = _fetch_many(
            list(cfg.large_50), start, end, min_rows=300, label="large_50",
        )
    if include_hibeta:
        sleeve_data["equity_hibeta"] = _compute_hibeta_universe(
            cfg, spy_close, start=start, end=end,
        )
    if include_gold:
        sleeve_data["gold"] = _fetch_many(
            list(cfg.gold_tickers), start, end, min_rows=300, label="gold",
        )

    ltp = compute_live_portfolio(
        strategy=strategy,
        spy_close=spy_close,
        sleeve_data=sleeve_data,
        cfg=cfg,
    )

    # Shape matches the /api/ingest/live-portfolio body the API endpoint
    # expects. Keeping the structure flat (header + decisions) so the
    # API can insert into strategy_runs + strategy_decisions in one
    # transaction.
    return {
        "strategy": ltp.strategy,
        "run_id": str(ltp.run_id),
        "mode": "live",
        "as_of_utc": ltp.as_of_utc.isoformat(),
        "uploaded_by": "tradepro-live-portfolio",
        "summary": ltp.to_summary_json(),
        "regime_state": ltp.regime_state,
        "decisions": [
            {
                "sleeve": d.sleeve,
                "symbol": d.symbol,
                "target_weight": d.target_weight,
                "signal": d.signal,
                "regime_pass": d.regime_pass,
                "vol": d.vol,
                "risk_class": None,  # populated by risk module Phase 2
                "detail": d.detail,
            }
            for d in ltp.decisions
        ],
    }


def _push(payload: dict[str, Any]) -> int:
    base = get_secret("api-base-url") or get_secret("api-url")
    token = (
        get_secret("ingest-api-token")
        or get_secret("ingest-token")
        or get_secret("api-token")
    )
    if not base or not token:
        log.error("push needs api-base-url + ingest token — none found in env / SM / credentials")
        return 2
    url = f"{base.rstrip('/')}/api/ingest/live-portfolio"
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=60,
        )
    except requests.RequestException as exc:
        log.error("push HTTP failed: %s", exc)
        return 3
    if not 200 <= resp.status_code < 300:
        log.error("push HTTP %d: %s", resp.status_code, resp.text[:400])
        return 4
    log.info("push ok: %s", resp.text[:300])
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="tradepro-live-portfolio",
        description=(
            "Slow-loop runner. Computes today's target portfolio for the "
            "trader's algo and (with --push) persists into strategy_runs "
            "+ strategy_decisions via /api/ingest/live-portfolio."
        ),
    )
    p.add_argument("--strategy", default="ichimoku_equity")
    p.add_argument("--no-large", action="store_true", help="Skip the large_50 sleeve.")
    p.add_argument("--no-hibeta", action="store_true", help="Skip the high-beta sleeve.")
    p.add_argument("--no-gold", action="store_true", help="Skip the gold sleeve.")
    p.add_argument("--push", action="store_true",
                   help="POST to /api/ingest/live-portfolio after writing the JSON.")
    p.add_argument("--out", default=str(DEFAULT_OUT_PATH),
                   help=f"Local JSON output (default {DEFAULT_OUT_PATH}).")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        payload = build_payload(
            strategy=args.strategy,
            include_large=not args.no_large,
            include_hibeta=not args.no_hibeta,
            include_gold=not args.no_gold,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("slow loop failed: %s", exc)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    log.info("wrote %s (%.1f KB)", out_path, out_path.stat().st_size / 1024)

    # Compact summary.
    summary = payload["summary"]
    long_decisions = [d for d in payload["decisions"] if d["target_weight"] > 0]
    print()
    print("=== LIVE PORTFOLIO — TODAY'S ALGO TARGET ===")
    print(f"strategy   : {payload['strategy']}")
    print(f"run_id     : {payload['run_id']}")
    print(f"as_of      : {payload['as_of_utc']}")
    print(f"bar_ts     : {summary['bar_ts']}  (most-recent complete daily bar)")
    print(f"regime     : {payload['regime_state']}   vol_scalar {summary['vol_scalar']:.2f}")
    for s in summary["sleeves"]:
        print(f"  sleeve {s['name']:14} n_long {s['n_long']:3} / {s['n_tickers']:3}"
              f"   ensemble_w {s['ensemble_weight']*100:5.1f}%"
              f"   {s.get('note') or ''}")
    print(f"\n{len(long_decisions)} long positions ({len(payload['decisions'])} decisions total):")
    long_decisions.sort(key=lambda d: -d["target_weight"])
    for d in long_decisions[:25]:
        det = d["detail"]
        print(f"  {d['symbol']:8} {d['sleeve']:14} target {d['target_weight']*100:5.2f}%"
              f"  cloud={det.get('cloud_position','?'):6} tk={det.get('tk_cross','?'):8}"
              f"  vol {d.get('vol') or 0:5.1f}%")
    if len(long_decisions) > 25:
        print(f"  ... + {len(long_decisions) - 25} more")

    if args.push:
        rc = _push(payload)
        if rc != 0:
            return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
