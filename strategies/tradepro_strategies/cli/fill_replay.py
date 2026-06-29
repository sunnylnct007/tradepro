"""tradepro-fill-replay — replay ACTUAL OMS fills to measure entry quality.

Turns the ad-hoc entry-extension analysis into a repeatable instrument: for a
strategy's real FILLED BUYs, measure WHERE each entry landed (% over the
200-SMA, ATR over Kijun) and the OUTCOME, split by universe, plus a
one-shot-vs-staggered counterfactual. This is the honest diagnosis tool for
LIVE losses — the backtest models the strategy LOGIC; this replays what we
ACTUALLY did (entry timing, concentration, seeding).

It is what refuted the over-extension hypothesis (2026-06-28): on the real
fills, entry-extension did NOT separate winners from losers, and staggering the
one-shot high_beta basket changed nothing — the loss was universe AMPLITUDE.

    uv run tradepro-fill-replay --strategy ichimoku_equity_ibkr --json
    uv run tradepro-fill-replay --strategy ichimoku_equity_ibkr --push

Emits one JSON artifact: per-universe summary (one-shot vs staggered return,
win-rate, extension↔return correlation, avg extension of losers vs winners) +
per-symbol rows. With --push, POSTs it to /api/ingest/fill-replay so the
cockpit can render it (same store pattern as equity-pipeline).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import logging
import os

log = logging.getLogger("tradepro.fill_replay")

# 10-trading-day DCA window for the "would staggering have helped?" counterfactual.
_STAGGER_DAYS = 10
_MIN_HISTORY = 200  # need 200 bars for the SMA200 at entry


def _classify(sym: str, universes: dict[str, set[str]]) -> tuple[str | None, str | None]:
    """Map an OMS symbol → (universe, bare_ticker). OMS stores large_50 as
    e.g. ``KO_US_EQ`` and high_beta as bare ``SMCI``; IG FX/CFD epics
    (contain '.') are not equities → skipped."""
    if sym.endswith("_US_EQ"):
        tk = sym[:-6]
        for name, members in universes.items():
            if tk in members:
                return name, tk
        return "other", tk
    if "." in sym:
        return None, None
    for name, members in universes.items():
        if sym in members:
            return name, sym
    return None, None


def _load_daily(cache_dir: str, sym: str):
    import pandas as pd

    files = sorted(glob.glob(f"{cache_dir}/{sym}/1d/*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files])
    df.columns = [c.lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex):
        for c in ("date", "timestamp", "time", "datetime"):
            if c in df.columns:
                df = df.set_index(pd.to_datetime(df[c]))
                break
    df.index = pd.to_datetime(df.index)
    try:
        df.index = df.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return df[~df.index.duplicated(keep="last")].sort_index()


def _fetch_universe(base: str, name: str, token: str | None) -> set[str]:
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(f"{base}/api/universes/{name}", headers=headers, timeout=20)
    r.raise_for_status()
    return {s["ticker"] for s in r.json().get("symbols", []) if s.get("effective", True)}


def replay(
    strategy: str,
    base: str,
    token: str | None,
    universe_names: list[str],
    cache_dir: str,
) -> dict:
    """Compute the fill-replay artifact for one strategy."""
    import numpy as np
    import pandas as pd
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    universes = {n: _fetch_universe(base, n, token) for n in universe_names}

    d = requests.get(f"{base}/api/oms/orders",
                     params={"strategyId": strategy, "limit": 5000},
                     headers=headers, timeout=60).json()
    orders = d if isinstance(d, list) else (d.get("orders") or d.get("rows") or [])
    buys = [o for o in orders
            if o.get("side") == "BUY" and o.get("state") == "FILLED" and o.get("avgFillPrice")]

    def ts(o):
        return o.get("lastStateChangeAtUtc") or o.get("createdAtUtc")

    # earliest filled BUY per (universe, ticker) = the entry that matters
    firsts: dict[tuple[str, str], dict] = {}
    for o in sorted(buys, key=lambda x: ts(x) or ""):
        run, tk = _classify(o["symbol"], universes)
        if run and (run, tk) not in firsts:
            firsts[(run, tk)] = o

    rows, missing = [], []
    for (run, tk), o in firsts.items():
        df = _load_daily(cache_dir, tk)
        if df is None or "close" not in df.columns or len(df) < _MIN_HISTORY:
            missing.append(o["symbol"])
            continue
        fd = pd.to_datetime(ts(o))
        fd = fd.tz_localize(None) if fd.tz else fd
        sub = df[df.index <= fd]
        if len(sub) < _MIN_HISTORY:
            missing.append(o["symbol"])
            continue
        close = float(sub["close"].iloc[-1])
        sma200 = float(sub["close"].tail(200).mean())
        ext = (close / sma200 - 1) * 100 if sma200 else float("nan")
        hi, lo, cl = sub["high"], sub["low"], sub["close"]
        tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        kij = (float(hi.tail(32).max()) + float(lo.tail(32).min())) / 2
        atr_over = (close - kij) / atr if atr > 0 else float("nan")
        last = float(df["close"].iloc[-1])
        entry_px = float(o["avgFillPrice"])
        ret = (last / entry_px - 1) * 100
        fwd = df[df.index >= fd].head(_STAGGER_DAYS)
        stag_px = float(fwd["close"].mean()) if len(fwd) else close
        stag = (last / stag_px - 1) * 100
        rows.append({
            "symbol": tk, "universe": run, "entry_date": str(fd.date()),
            "ext_pct": round(ext, 1), "atr_over_kijun": round(atr_over, 1),
            "return_pct": round(ret, 1), "staggered_return_pct": round(stag, 1),
        })

    summaries = {}
    for name in universe_names:
        r = [x for x in rows if x["universe"] == name]
        if not r:
            continue
        ext = np.array([x["ext_pct"] for x in r], dtype=float)
        ret = np.array([x["return_pct"] for x in r], dtype=float)
        stag = np.array([x["staggered_return_pct"] for x in r], dtype=float)
        corr = float(np.corrcoef(ext, ret)[0, 1]) if len(r) > 1 else float("nan")
        summaries[name] = {
            "n": len(r),
            "entry_dates": sorted({x["entry_date"] for x in r}),
            "one_shot_return_pct": round(float(ret.mean()), 1),
            "staggered_return_pct": round(float(stag.mean()), 1),
            "win_rate_pct": round(float((ret > 0).mean() * 100), 0),
            "ext_return_corr": round(corr, 2),
            "ext_losers_avg": round(float(ext[ret < 0].mean()), 1) if (ret < 0).any() else None,
            "ext_winners_avg": round(float(ext[ret >= 0].mean()), 1) if (ret >= 0).any() else None,
        }

    return {
        "kind": "fill_replay",
        "strategy": strategy,
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "stagger_days": _STAGGER_DAYS,
        "universes": summaries,
        "rows": sorted(rows, key=lambda x: x["return_pct"]),
        "missing": missing,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="tradepro-fill-replay")
    p.add_argument("--strategy", default="ichimoku_equity_ibkr", help="OMS strategyId to replay")
    p.add_argument("--api-base", default=None, help="API base (else from credentials)")
    p.add_argument("--universes", default="high_beta,large_50",
                   help="comma-separated universe names to classify entries against")
    p.add_argument("--cache-dir", default=os.path.expanduser("~/.tradepro/bar_cache/us_etf"))
    p.add_argument("--label", default="latest", help="artifact label (for side-by-side runs)")
    p.add_argument("--json", action="store_true", help="print the artifact JSON to stdout")
    p.add_argument("--push", action="store_true",
                   help="POST the artifact to /api/ingest/fill-replay")
    args = p.parse_args()

    from . import push_to_api as _pta

    base, token = args.api_base, None
    if not base:
        base, token = _pta.load_credentials()
    base = base.rstrip("/")

    artifact = replay(
        args.strategy, base, token,
        [u.strip() for u in args.universes.split(",") if u.strip()],
        args.cache_dir,
    )

    # Console summary (always) — the honest headline.
    for name, s in artifact["universes"].items():
        log.info(
            "%s  n=%d  one-shot %+.1f%% / staggered %+.1f%%  win %.0f%%  "
            "corr(ext,ret) %+.2f  ext losers=%s winners=%s",
            name, s["n"], s["one_shot_return_pct"], s["staggered_return_pct"],
            s["win_rate_pct"], s["ext_return_corr"], s["ext_losers_avg"], s["ext_winners_avg"],
        )
    if artifact["missing"]:
        log.info("(%d symbols skipped: no cache / <200 bars)", len(artifact["missing"]))

    if args.json:
        import json
        print(json.dumps(artifact, indent=2))

    if args.push:
        if not token:
            _, token = _pta.load_credentials()
        _pta.push("fill-replay", {
            "strategy": args.strategy,
            "label": args.label,
            "uploaded_by": os.uname().nodename,
            "artifact": artifact,
        }, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
