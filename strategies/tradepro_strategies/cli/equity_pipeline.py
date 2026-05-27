"""tradepro-equity-pipeline — full trader-spec equity backtest.

End-to-end port of ``docs/main 4.py`` (the trader's StrategyRunner).
Mirrors the trader's pipeline 1-to-1 so the demo can show the
"validated strategy" view they actually designed, instead of just the
fragments (single-sleeve / no walk-forward / no Monte Carlo) the
existing ``tradepro-quant-backtest`` exposes.

Stages
------
1. Fetch SPY benchmark (cache-backed; see tradepro_strategies.cache).
2. Build a SPY 200-SMA RegimeFilter (bull/bear gate for the hibeta sleeve).
3. Fetch the three sleeve universes:
     - large_50 — hand-curated mega-cap list (QuantEngineConfig.large_50)
     - high_beta — UniverseBuilder.build_high_beta β>1.5 vs SPY
       (S&P 500 + 400 minus crypto-beta names)
     - gold — single ticker (GLD)
4. Construct one Sleeve per universe; regime gate applied only to hibeta.
5. Ensemble: equal-weight + portfolio-level vol target (Hurst-Ooi-Pedersen).
6. Walk-forward OOS validation across the 5 windows in QuantEngineConfig.
7. Block-bootstrap Monte Carlo (10k sims × 10 years) on OOS returns.
8. Emit one JSON artifact with:
     - In-sample summary (Sharpe / Sortino / CAGR / MaxDD / Calmar)
     - OOS aggregate summary + per-window detail
     - SPY benchmark for the same window
     - Plot-source data (equity / drawdown / sleeve cumulative /
       gross-exposure / Monte Carlo paths quantiles) so the frontend
       can render the trader's 4-panel backtest chart + MC fan chart
       without re-running the pipeline.

Usage
-----
    tradepro-equity-pipeline                       # full run, default config
    tradepro-equity-pipeline --start 2020-01-01    # override window
    tradepro-equity-pipeline --no-mc               # skip Monte Carlo
    tradepro-equity-pipeline --no-hibeta           # skip hibeta sleeve
    tradepro-equity-pipeline --out path/to/x.json  # custom output path

Default output path: ~/.tradepro/cache/equity_pipeline_latest.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import requests

from ..cache import ensure_cached
from ..quant_engine import (
    Ensemble,
    MonteCarloSimulator,
    QuantEngineConfig,
    RegimeFilter,
    Sleeve,
    WalkForwardValidator,
    build_high_beta,
)
from ..quant_engine.portfolio_metrics import summarise as _summarise
from ..secrets import get_secret

log = logging.getLogger("tradepro.cli.equity_pipeline")

DEFAULT_OUT_PATH = Path.home() / ".tradepro" / "cache" / "equity_pipeline_latest.json"


def _fetch_one(ticker: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """Wrap cache.ensure_cached with the trader's "Capitalised columns"
    convention (Open / High / Low / Close). Returns None if no usable
    history (silent — caller decides whether that's fatal)."""
    try:
        df = ensure_cached("yahoo", ticker, start, end, "1d")
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch %s failed: %s", ticker, exc)
        return None
    if df is None or df.empty:
        return None
    # Cache stores lower-case ohlc; trader's Sleeve expects High/Low/Close.
    col_map = {c.lower(): c for c in df.columns}
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(col_map):
        return None
    return df.rename(columns={
        col_map["open"]: "Open",
        col_map["high"]: "High",
        col_map["low"]: "Low",
        col_map["close"]: "Close",
    })


def _fetch_many(
    tickers: list[str],
    *,
    start: datetime,
    end: datetime,
    min_rows: int = 300,
    label: str = "",
) -> dict[str, pd.DataFrame]:
    """Bulk fetch, skipping failures + short histories. Reports progress
    every 25 tickers since the trader's reference runs 50+900 names and
    a silent block looks like a hang."""
    out: dict[str, pd.DataFrame] = {}
    for i, t in enumerate(tickers, 1):
        if i % 25 == 0:
            log.info("  %s: fetched %d/%d", label, i, len(tickers))
        df = _fetch_one(t, start, end)
        if df is None or len(df) < min_rows:
            continue
        out[t] = df
    log.info("%s: %d/%d tickers with usable history", label, len(out), len(tickers))
    return out


def _equity_to_records(equity: pd.Series) -> list[dict[str, Any]]:
    """Pandas series → list of {date, value} dicts for JSON."""
    return [
        {"date": idx.strftime("%Y-%m-%d"), "value": float(v)}
        for idx, v in equity.items()
        if pd.notna(v)
    ]


def _drawdown_records(equity: pd.Series) -> list[dict[str, Any]]:
    """Drawdown % series, same shape as equity records."""
    dd = (equity - equity.cummax()) / equity.cummax() * 100
    return [
        {"date": idx.strftime("%Y-%m-%d"), "value": float(v)}
        for idx, v in dd.items()
        if pd.notna(v)
    ]


def _mc_quantiles(paths: np.ndarray, quantiles: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)) -> dict[str, list[float]]:
    """Reduce the (n_sims, n_days+1) MC paths array to per-quantile
    bands so the JSON stays small enough to ship to the browser without
    GB-sized payloads."""
    qs = np.quantile(paths, list(quantiles), axis=0)  # shape (n_q, n_days+1)
    out: dict[str, list[float]] = {}
    for q, row in zip(quantiles, qs):
        key = f"q{int(q * 100):02d}"
        out[key] = [float(v) for v in row]
    return out


def run_pipeline(
    cfg: QuantEngineConfig | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    initial_capital: float | None = None,
    run_hibeta: bool = True,
    run_mc: bool = True,
    mc_n_sims: int = 10_000,
    mc_years: int = 10,
    mc_initial: float = 10_000.0,
    mc_seed: int | None = 42,
) -> dict[str, Any]:
    """Trader's StrategyRunner.run() ported. Returns the result dict
    that the CLI serialises to JSON.

    The shape is documented inline so the frontend can be wired
    against it without re-reading this function:

      {
        "as_of_utc": ISO timestamp of the run,
        "config": { ...flatten of QuantEngineConfig used },
        "in_sample": { sharpe / cagr / max_dd / ...summary stats },
        "walk_forward": {
          "summary": { ...aggregate OOS summary },
          "per_window": [{test_year, vol_scalar, sharpe, cagr, n_days}, ...],
        },
        "spy_benchmark": { ...same summary stats over the run window },
        "monte_carlo": {
          "n_sims": 10000,
          "years": 10,
          "initial": 10000,
          "summary": {final_median, final_p5, final_p95, ...},
          "fan_chart": { years_axis, q05/q25/q50/q75/q95 lists },
        } | null,
        "charts": {
          "equity": [{date, value}, ...],   # in-sample
          "oos_equity": [{date, value}, ...],
          "spy_equity": [{date, value}, ...],
          "drawdown": [{date, value}, ...],
          "spy_drawdown": [{date, value}, ...],
          "sleeve_cumulative": {
            "equity_large": [{date, value}, ...],
            "equity_hibeta": [...],
            "gold": [...],
          },
          "gross_exposure": [{date, value}, ...],
        },
        "sleeves_meta": [
          {name, n_tickers, fetched_at_utc, source, note},
          ...
        ],
        "timings_sec": { fetch_spy, fetch_large, fetch_hibeta, fetch_gold,
                        ensemble, walk_forward, monte_carlo, total },
      }
    """
    cfg = cfg or QuantEngineConfig()
    if start_date is None:
        start_date = cfg.start_date
    if end_date is None:
        end_date = cfg.end_date
    if initial_capital is None:
        initial_capital = cfg.initial_capital
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    timings: dict[str, float] = {}
    t_overall = time.monotonic()
    sleeves_meta: list[dict[str, Any]] = []

    # 1. Benchmark
    t0 = time.monotonic()
    log.info("fetching benchmark %s...", cfg.benchmark)
    spy_df = _fetch_one(cfg.benchmark, start, end)
    timings["fetch_spy"] = time.monotonic() - t0
    if spy_df is None or spy_df.empty:
        raise RuntimeError(f"benchmark {cfg.benchmark} unavailable — cache fetch returned empty")
    regime = (
        RegimeFilter(spy_df["Close"], cfg.regime_sma)
        if cfg.use_regime_filter else None
    )

    # 2. Sleeves
    log.info("fetching sleeve universes...")

    t0 = time.monotonic()
    large_data = _fetch_many(list(cfg.large_50), start=start, end=end, label="large_50")
    timings["fetch_large"] = time.monotonic() - t0
    sleeves_meta.append({
        "name": "equity_large", "n_tickers": len(large_data),
        "source": "QuantEngineConfig.large_50 (trader hand-curated)",
        "note": "no regime gate at sleeve level (already 'safe' names)",
    })

    hibeta_data: dict[str, pd.DataFrame] = {}
    if run_hibeta:
        t0 = time.monotonic()
        log.info("building high-beta universe (this may take a few minutes cold)...")
        try:
            from ..universes import wikipedia as wu
            candidates: set[str] = set()
            for uname in ("sp500", "sp400_midcap"):
                try:
                    for s in wu.fetch_universe(uname):
                        candidates.add(s.ticker)
                except wu.UniverseFetchError as e:
                    log.warning("%s scrape failed: %s", uname, e)
            candidates -= frozenset(cfg.crypto_exclude)
            sp_data = _fetch_many(
                sorted(candidates), start=start, end=end,
                min_rows=cfg.beta_lookback + 10, label="sp500+400",
            )
            closes = {t: df["Close"] for t, df in sp_data.items()}
            survivors = build_high_beta(
                spy_df["Close"], closes,
                min_beta=cfg.min_beta,
                beta_lookback=cfg.beta_lookback,
                crypto_exclude=cfg.crypto_exclude,
            )
            hibeta_data = {r.ticker: sp_data[r.ticker] for r in survivors}
        except Exception as exc:  # noqa: BLE001
            log.exception("high-beta sleeve build failed; continuing without it: %s", exc)
        timings["fetch_hibeta"] = time.monotonic() - t0
    sleeves_meta.append({
        "name": "equity_hibeta", "n_tickers": len(hibeta_data),
        "source": f"UniverseBuilder β>{cfg.min_beta} over {cfg.beta_lookback}d (S&P 500+400, ex-crypto)",
        "note": "regime gate (SPY 200-SMA) applied to suppress signals on bear days",
    })

    t0 = time.monotonic()
    gold_data = _fetch_many(list(cfg.gold_tickers), start=start, end=end, label="gold")
    timings["fetch_gold"] = time.monotonic() - t0
    sleeves_meta.append({
        "name": "gold", "n_tickers": len(gold_data),
        "source": "QuantEngineConfig.gold_tickers (GLD)",
        "note": "diversifier sleeve — uncorrelated to equities in stress regimes",
    })

    # 3. Construct sleeves (skip any with no data)
    sleeves: list[Sleeve] = []
    if large_data:
        sleeves.append(Sleeve("equity_large", large_data, cfg))
    if hibeta_data:
        sleeves.append(Sleeve("equity_hibeta", hibeta_data, cfg, regime=regime))
    if gold_data:
        sleeves.append(Sleeve("gold", gold_data, cfg))
    if not sleeves:
        raise RuntimeError("no sleeves built — every fetch returned empty")

    # 4. Ensemble + vol target
    log.info("running ensemble (%d sleeves)...", len(sleeves))
    t0 = time.monotonic()
    ensemble = Ensemble(sleeves, cfg, initial_capital=initial_capital)
    result = ensemble.run()
    timings["ensemble"] = time.monotonic() - t0

    # SPY benchmark equity rebased to the same starting capital + window
    spy_close_aligned = spy_df["Close"].reindex(result.equity.index, method="ffill")
    spy_returns = spy_close_aligned.pct_change().fillna(0.0)
    spy_equity = (1.0 + spy_returns).cumprod() * initial_capital
    spy_summary = _summarise(spy_equity, spy_returns)

    # 5. Walk-forward OOS
    log.info("walk-forward (%d windows)...", len(cfg.walk_forward_windows))
    t0 = time.monotonic()
    # Use equal-weight sleeve returns as the input — same as trader's
    # `ew_returns = result.sleeve_returns.mean(axis=1)`.
    sleeve_df = pd.DataFrame(result.sleeve_returns).fillna(0.0)
    ew_returns = sleeve_df.mean(axis=1)
    wf = WalkForwardValidator(
        ew_returns, target_vol=cfg.target_vol,
        max_leverage=cfg.max_leverage, windows=cfg.walk_forward_windows,
    )
    oos_returns, wf_windows = wf.run()
    oos_equity = (1.0 + oos_returns).cumprod() * initial_capital
    oos_summary = _summarise(oos_equity, oos_returns)
    timings["walk_forward"] = time.monotonic() - t0

    # 6. Monte Carlo (on OOS returns — matches trader's design)
    mc_payload = None
    if run_mc and not oos_returns.empty:
        log.info("monte carlo (%d sims × %d years)...", mc_n_sims, mc_years)
        t0 = time.monotonic()
        mc = MonteCarloSimulator(oos_returns, seed=mc_seed).run(
            initial=mc_initial, years=mc_years, n_sims=mc_n_sims,
        )
        timings["monte_carlo"] = time.monotonic() - t0
        years_axis = (np.arange(mc.paths.shape[1]) / 252).tolist()
        mc_payload = {
            "n_sims": mc.n_sims,
            "years": mc.years,
            "initial": mc_initial,
            "summary": mc.summary,
            "fan_chart": {
                "years_axis": years_axis,
                **_mc_quantiles(mc.paths),
            },
        }

    # 7. Plot-source data (charts panel)
    gross_exposure_series: pd.Series
    if hasattr(result, "vol_scalar") and result.vol_scalar is not None:
        # Each active sleeve contributes 1/n weight; multiply by vol scalar
        # to get the trader's "effective gross exposure post vol-target".
        gross_per_bar = sleeve_df.abs().sum(axis=1) / max(len(sleeves), 1)
        gross_exposure_series = (gross_per_bar * result.vol_scalar).fillna(0.0)
    else:
        gross_exposure_series = pd.Series(dtype=float)

    charts = {
        "equity": _equity_to_records(result.equity),
        "oos_equity": _equity_to_records(oos_equity),
        "spy_equity": _equity_to_records(spy_equity),
        "drawdown": _drawdown_records(result.equity),
        "spy_drawdown": _drawdown_records(spy_equity),
        "sleeve_cumulative": {
            name: _equity_to_records((1.0 + series).cumprod())
            for name, series in result.sleeve_returns.items()
        },
        "gross_exposure": [
            {"date": idx.strftime("%Y-%m-%d"), "value": float(v) * 100}
            for idx, v in gross_exposure_series.items()
            if pd.notna(v)
        ],
    }

    timings["total"] = time.monotonic() - t_overall

    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "start_date": start_date, "end_date": end_date,
            "initial_capital": initial_capital,
            "benchmark": cfg.benchmark,
            "tenkan": cfg.tenkan, "kijun": cfg.kijun,
            "senkou_b": cfg.senkou_b, "displacement": cfg.displacement,
            "sleeve_large": cfg.sleeve_large,
            "sleeve_hibeta": cfg.sleeve_hibeta,
            "sleeve_gold": cfg.sleeve_gold,
            "min_beta": cfg.min_beta, "beta_lookback": cfg.beta_lookback,
            "regime_sma": cfg.regime_sma,
            "use_regime_filter": cfg.use_regime_filter,
            "target_vol": cfg.target_vol,
            "max_leverage": cfg.max_leverage,
            "vol_lookback": cfg.vol_lookback,
        },
        "in_sample": result.summary,
        "walk_forward": {
            "summary": oos_summary,
            "per_window": [
                {
                    "test_year": w.test_year,
                    "vol_scalar": float(w.vol_scalar),
                    "sharpe": float(w.test_sharpe),
                    "cagr_pct": float(w.test_cagr_pct),
                    "n_days": int(w.n_test_days),
                }
                for w in wf_windows
            ],
        },
        "spy_benchmark": spy_summary,
        "monte_carlo": mc_payload,
        "charts": charts,
        "sleeves_meta": sleeves_meta,
        "timings_sec": {k: round(v, 2) for k, v in timings.items()},
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(
        prog="tradepro-equity-pipeline",
        description=(
            "Full trader-spec equity backtest pipeline (port of "
            "docs/main 4.py). Runs Ichimoku sleeves + ensemble + "
            "walk-forward OOS + Monte Carlo, emits a JSON artifact "
            "the UI can render into the trader's 4-panel chart + MC fan."
        ),
    )
    p.add_argument("--start", default=None, help="Override config start date (YYYY-MM-DD).")
    p.add_argument("--end", default=None, help="Override config end date (YYYY-MM-DD).")
    p.add_argument("--capital", type=float, default=None, help="Override initial capital (USD).")
    p.add_argument("--no-hibeta", action="store_true", help="Skip the high-beta sleeve (~ few min cold).")
    p.add_argument("--no-mc", action="store_true", help="Skip the Monte Carlo step.")
    p.add_argument("--mc-sims", type=int, default=10_000, help="Monte Carlo sim count (default 10000).")
    p.add_argument("--mc-years", type=int, default=10, help="Monte Carlo horizon in years (default 10).")
    p.add_argument(
        "--out", default=str(DEFAULT_OUT_PATH), metavar="PATH",
        help=f"JSON output path (default {DEFAULT_OUT_PATH}).",
    )
    p.add_argument("--print", action="store_true", help="Also print the JSON to stdout.")
    p.add_argument(
        "--push", action="store_true",
        help=(
            "Also POST the artifact to /api/ingest/equity-pipeline so "
            "the UI's strategy validation page can render it. Uses "
            "ingest-token from env / SM / ~/.tradepro/credentials."
        ),
    )
    p.add_argument(
        "--label", default="latest",
        help="Artifact label (default 'latest'). Picking a different label "
             "keeps multiple runs side-by-side for A/B comparison.",
    )
    p.add_argument(
        "--strategy-id", default="ichimoku_equity",
        help="Strategy id this artifact is for (default ichimoku_equity).",
    )
    p.add_argument(
        "--note", default=None,
        help="Free-text note stored with the artifact (e.g. 'weekly refresh').",
    )
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        result = run_pipeline(
            start_date=args.start, end_date=args.end,
            initial_capital=args.capital,
            run_hibeta=not args.no_hibeta,
            run_mc=not args.no_mc,
            mc_n_sims=args.mc_sims,
            mc_years=args.mc_years,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("pipeline failed: %s", exc)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    log.info("wrote %s (%.1f KB)", out_path, out_path.stat().st_size / 1024)

    if args.push:
        base = get_secret("api-base-url") or get_secret("api-url")
        token = (
            get_secret("ingest-api-token")
            or get_secret("ingest-token")
            or get_secret("api-token")  # last-resort: same token for both schemes
        )
        if not base or not token:
            log.error(
                "push requested but credentials missing — set "
                "TRADEPRO_API_BASE_URL + TRADEPRO_INGEST_API_TOKEN "
                "or populate ~/.tradepro/credentials",
            )
            return 2
        url = f"{base.rstrip('/')}/api/ingest/equity-pipeline"
        payload = {
            "strategy": args.strategy_id,
            "label": args.label,
            "uploaded_by": "tradepro-equity-pipeline",
            "note": args.note,
            "artifact": result,
        }
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

    # Compact CLI-friendly summary.
    sumr = result["in_sample"]
    wf = result["walk_forward"]["summary"]
    spy = result["spy_benchmark"]
    sleeves_text = ", ".join(f"{s['name']}({s['n_tickers']})" for s in result["sleeves_meta"])
    print("\n=== EQUITY PIPELINE — TRADER SPEC ===")
    print(f"window               : {result['config']['start_date']} -> {result['config']['end_date']}")
    print(f"sleeves              : {sleeves_text}")
    print(f"in-sample sharpe     : {sumr.get('sharpe'):.2f}   cagr {sumr.get('cagr_pct')}%   max-dd {sumr.get('max_drawdown_pct')}%")
    print(f"walk-forward sharpe  : {wf.get('sharpe'):.2f}   cagr {wf.get('cagr_pct')}%   max-dd {wf.get('max_drawdown_pct')}%")
    print(f"spy b&h sharpe       : {spy.get('sharpe'):.2f}   cagr {spy.get('cagr_pct')}%   max-dd {spy.get('max_drawdown_pct')}%")
    if result["monte_carlo"]:
        mc = result["monte_carlo"]["summary"]
        print(f"monte carlo ({result['monte_carlo']['n_sims']:,} sims, {result['monte_carlo']['years']}y, ${result['monte_carlo']['initial']:,.0f} → ...):")
        for k, v in mc.items():
            print(f"  {k}: {v}")
    print(f"\ntimings (s): {result['timings_sec']}")
    print(f"\nartifact: {out_path}")
    if args.print:
        print()
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
