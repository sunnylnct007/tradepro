"""tradepro-quant-backtest — run one quant-engine backtest end-to-end.

The CLI is the worker half of the `/api/quant/backtest/run` flow:
the .NET API enqueues a SessionRequest with `kind="backtest"`, the
Mac daemon (paper_daemon) claims the row and shells out to this CLI
with the payload. The CLI runs the requested sleeve + ensemble +
Monte Carlo, builds the two trader-anchor charts via the viz
framework, and prints a `result_summary` JSON block to stdout. The
daemon picks the JSON up and POSTs it back as a session completion.

Payload schema (JSON dict)::

    {
      "kind": "backtest",                  # echoed; pinned to "backtest"
      "strategy": "ichimoku_equity",       # sleeve strategy identifier
      "symbols": ["AAPL", "MSFT", "GLD"],  # tickers (single sleeve today)
      "start": "2018-01-01",
      "end":   "2024-12-31",
      "initial_capital": 100000.0,
      "benchmark": "SPY",
      "monte_carlo": {                     # optional; defaults below
        "n_sims": 500,
        "years":  5,
        "seed":   42
      },
      "label": "Trader weekly backtest"    # optional; UI display only
    }

Output (printed to stdout)::

    {
      "kind": "backtest",
      "summary": {...},                    # ensemble + MC summary
      "charts": {                          # frontend Session Detail
         "backtest_4panel":   {...},       # picks these up via
         "monte_carlo_fan":   {...}        # extractCharts() helper
      },
      "strategies": [                      # per-strategy block so the
        {"strategy_id": "<label>",         # Bars/Decisions/etc. tabs
         "equity": ..., "fills_count": 0,  # render uniformly with
         "positions": []}                  # paper sessions
      ]
    }

The output is wrapped in a top-level JSON object so the daemon's
``_extract_*`` helper can scan stdout. The two block markers
``BEGIN_QUANT_BACKTEST_RESULT`` / ``END_QUANT_BACKTEST_RESULT`` make
the extraction trivial (and resilient to noisy logging from
yfinance / plotly that may also write to stdout).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from ..data import DataRequest, load_candles
from ..quant_engine.config import QuantEngineConfig
from ..quant_engine.ensemble import Ensemble, EnsembleResult
from ..quant_engine.monte_carlo import MonteCarloResult, MonteCarloSimulator
from ..quant_engine.portfolio_metrics import summarise
from ..quant_engine.sleeve import Sleeve
from ..viz import build_chart

# Phase D-3/E for the quant backtest path. The `_run_preflight` helper
# is split out so a BDD scenario can exercise the hash combination
# logic without spinning up the engine. Late imports inside the helper
# keep the CLI cheap to import for callers that don't preflight.


log = logging.getLogger("tradepro.cli.quant_backtest")

# Backtests tolerate up to this fraction of isolated missing sessions before the
# preflight hard-blocks. Small harvest gaps (a missing session or two) proceed
# with the gap RECORDED on the report (fail-visible); a real hole still aborts.
_DEFAULT_MAX_MISSING_FRACTION = 0.01


RESULT_BEGIN = "BEGIN_QUANT_BACKTEST_RESULT"
RESULT_END = "END_QUANT_BACKTEST_RESULT"


# ---------------------------------------------------------------------------
# Payload loading
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-quant-backtest",
        description="Run one quant-engine backtest + Monte Carlo and emit "
                    "a result_summary JSON to stdout.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--payload-json",
        metavar="PATH",
        help="Path to a JSON file describing the backtest. Use '-' for stdin.",
    )
    src.add_argument(
        "--payload",
        metavar="JSON",
        help="Inline JSON string (handy for tests; prefer --payload-json in prod).",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    # request_id is opaque to the engine — we just echo it back into
    # result_summary so the daemon can correlate the CLI's stdout
    # with the session_request row when posting completion.
    p.add_argument(
        "--request-id",
        default=None,
        help="Opaque request id echoed into the result_summary envelope.",
    )
    # Phase D-3 / E preflight flags (override the payload values).
    # When --data-asset is set the CLI runs BarStore.get for every
    # symbol in the range, auto-stamps the data_state_hash on the
    # report, and refuses incomplete coverage (Phase E) unless
    # --allow-incomplete-data is also supplied.
    p.add_argument(
        "--data-asset", default=None,
        help="Asset class for the BarStore preflight (e.g. 'us_etf'). "
             "Overrides payload.data_asset.",
    )
    p.add_argument(
        "--data-resolution", default=None,
        help="Resolution for the preflight (default 1d). "
             "Overrides payload.data_resolution.",
    )
    p.add_argument(
        "--data-api-base", default=None,
        help="API base for telemetry + provider preferences. Without it the "
             "preflight uses local JSONL telemetry + hardcoded chain.",
    )
    p.add_argument(
        "--allow-incomplete-data", action="store_true",
        help="Override Phase E hard-block. Preflight still captures the "
             "data_state_hash but partial coverage no longer refuses the run.",
    )
    p.add_argument(
        "--legacy-cache", action="store_true",
        help="Opt-out of BarStore preflight and use the legacy cache.py path. "
             "Preserved for parity testing + emergency rollback; every "
             "production caller should leave this off.",
    )
    return p.parse_args(argv)


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        if args.payload_json == "-":
            raw = sys.stdin.read()
        else:
            with open(args.payload_json, encoding="utf-8") as fh:
                raw = fh.read()
    else:
        raw = args.payload
    if not raw or not raw.strip():
        raise SystemExit("ERROR: empty payload")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: invalid JSON payload: {exc}") from exc


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def _combine_data_state_hashes(per_symbol: dict[str, str]) -> str:
    """Combine per-symbol BarStore hashes into a single fingerprint.

    The D-2 cockpit chip indexes one hash per backtest report. For a
    quant backtest (multi-symbol by definition) we hash the sorted
    'symbol:per_symbol_hash' lines. Same symbols + same data → same
    fingerprint; one symbol's cache rebuild shifts the combined hash."""
    import hashlib
    lines = sorted(f"{sym}:{h}" for sym, h in per_symbol.items())
    if not lines:
        return "EMPTY_DATA_STATE"
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _run_preflight(
    symbols: list[str],
    start: datetime,
    end: datetime,
    *,
    asset_class: str | None = None,
    resolution: str,
    api_base: str | None,
    allow_incomplete: bool,
) -> dict[str, Any]:
    """Run `preflight_data_state` for every symbol in the backtest.

    ``asset_class`` is a per-RUN override applied to every symbol; when
    None (the default), each symbol is auto-resolved via
    ``asset_class_resolver``. Mixed universes ("AAPL,SPY,EURUSD=X")
    therefore resolve correctly with no operator intervention.

    Returns a dict to merge into the result_summary's `data_state`
    block, including a ``resolved_classes`` map so the cockpit can
    show "AAPL → us_equity, SPY → us_etf, EURUSD=X → fx_spot" rather
    than leaving the operator guessing.

    On any symbol's incomplete coverage (and require_complete),
    re-raises ``IncompleteDataError`` with the list of missing
    sessions so the caller can stop the run with a structured exit."""

    from ..bar_cache import IncompleteDataError, preflight_data_state
    from ..bar_cache.asset_class_resolver import (
        ASSET_CLASS_UNKNOWN,
        resolve_asset_class,
    )

    per_symbol_states: dict[str, dict[str, Any]] = {}
    per_symbol_hashes: dict[str, str] = {}
    resolved_classes: dict[str, str] = {}
    coverage_complete = True

    start_utc = (
        start.replace(tzinfo=UTC) if start.tzinfo is None else start
    )
    end_utc = end.replace(tzinfo=UTC) if end.tzinfo is None else end

    for sym in symbols:
        # Resolve per-symbol when no run-level override was supplied.
        ac = asset_class or resolve_asset_class(sym)
        if ac == ASSET_CLASS_UNKNOWN:
            raise ValueError(
                f"asset_class could not be auto-resolved for symbol {sym!r}; "
                f"pass `asset_class=` on the run or `--legacy-cache` to skip."
            )
        resolved_classes[sym] = ac
        try:
            pf = preflight_data_state(
                canonical=sym,
                asset_class=ac,
                resolution=resolution,
                start=start_utc,
                end=end_utc,
                require_complete=not allow_incomplete,
                # Tolerate up to 1% isolated missing sessions (a harvest gap)
                # rather than aborting the whole run on one missing bar — the
                # gap is still recorded on the report (fail-visible). A larger
                # hole still hard-fails. --allow-incomplete-data bypasses entirely.
                max_missing_fraction=_DEFAULT_MAX_MISSING_FRACTION,
                api_base=api_base,
                fetched_by=f"quant-backtest:{ac}",
            )
        except IncompleteDataError:
            # Bubble up — caller wraps as exit 3 with the structured
            # error payload from IncompleteDataError.to_dict().
            raise
        per_symbol_hashes[sym] = pf.data_state_hash
        per_symbol_states[sym] = pf.to_report_dict()
        if not pf.coverage_complete:
            coverage_complete = False

    combined_hash = _combine_data_state_hashes(per_symbol_hashes)
    return {
        "data_state_hash": combined_hash,
        "data_state": {
            "hash": combined_hash,
            "coverage_complete": coverage_complete,
            # `asset_class` here is the run-level override (None when
            # we auto-resolved per symbol). The per-symbol map below is
            # the source of truth for "what got hashed".
            "asset_class": asset_class,
            "resolution": resolution,
            "per_symbol_states": per_symbol_states,
            "resolved_classes": resolved_classes,
            "resolved_by": "explicit" if asset_class else "auto-resolver",
        },
    }


def _load_sleeve_data(
    symbols: list[str],
    start: datetime,
    end: datetime,
    provider: str = "yahoo",
) -> dict[str, Any]:
    """Fetch OHLC bars for a single sleeve. Returns a dict[ticker, DataFrame]
    shaped how Sleeve.run expects (capitalised columns: Open/High/Low/Close).

    Symbols that come back empty are dropped with a warning so a single
    typo doesn't sink the whole backtest. The caller must check at least
    one survives.
    """
    import pandas as pd

    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        req = DataRequest(symbol=sym, start=start, end=end, provider=provider)
        df = load_candles(req)
        if df.empty:
            log.warning("symbol %s returned no bars; skipping", sym)
            continue
        # Sleeve.run expects capitalised column names (High/Low/Close).
        # data.py lower-cases them — adapt here.
        renamed = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "adj_close": "AdjClose", "volume": "Volume",
        })
        out[sym] = renamed
    return out


def _spy_benchmark(start: datetime, end: datetime, initial_capital: float) -> tuple[Any, dict]:
    """Return ``(equity_series, summary_dict)`` for the SPY buy-and-hold benchmark.

    The 4-panel chart wants both an equity curve and a summary metric
    block for the title. We compound a flat buy-and-hold on SPY's
    daily close, normalised to ``initial_capital``.
    """
    import pandas as pd

    req = DataRequest(symbol="SPY", start=start, end=end, provider="yahoo")
    df = load_candles(req)
    if df.empty:
        # Synthetic flat benchmark so the chart still renders rather than
        # error out — operator sees "SPY data missing" in the title.
        idx = pd.date_range(start, end, freq="B")
        flat = pd.Series(initial_capital, index=idx)
        return flat, {"cagr_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    close = df["close"]
    rets = close.pct_change().fillna(0.0)
    equity = (1.0 + rets).cumprod() * initial_capital
    return equity, summarise(equity, rets)


# ---------------------------------------------------------------------------
# Backtest orchestration
# ---------------------------------------------------------------------------

def _run_backtest(
    payload: dict[str, Any],
) -> tuple[EnsembleResult, MonteCarloResult, Any, dict, dict[str, Any] | None]:
    """Execute the ensemble + Monte Carlo and return the four objects
    the chart builders consume.

    Returns ``(ensemble_result, mc_result, spy_equity, spy_summary,
    preflight_payload)``. ``preflight_payload`` is None when the
    payload omits ``data_asset`` (legacy callers stay unchanged);
    otherwise it carries the combined data_state_hash + per-symbol
    BarStore states for stamping on the report.
    """
    symbols = list(payload.get("symbols") or [])
    if not symbols:
        raise SystemExit("ERROR: payload.symbols must be a non-empty list")

    start = datetime.fromisoformat(str(payload.get("start", "2020-01-01")))
    end = datetime.fromisoformat(str(payload.get("end", "2024-12-31")))
    initial_capital = float(payload.get("initial_capital", 100_000.0))

    # Phase E preflight runs by DEFAULT. Per-symbol auto-resolution
    # picks the right asset_class (us_equity / us_etf / fx_spot /
    # uk_equity / crypto) for each ticker. payload.data_asset can
    # override the resolver per-run; payload.legacy_cache=true opts
    # out entirely (parity testing / emergency rollback).
    preflight_payload: dict[str, Any] | None = None
    if not bool(payload.get("legacy_cache", False)):
        from ..bar_cache import IncompleteDataError
        data_asset = payload.get("data_asset")  # None ⇒ auto-resolve
        try:
            preflight_payload = _run_preflight(
                symbols=symbols,
                start=start,
                end=end,
                asset_class=str(data_asset) if data_asset else None,
                resolution=str(payload.get("data_resolution", "1d")),
                api_base=payload.get("data_api_base"),
                allow_incomplete=bool(payload.get("allow_incomplete_data", False)),
            )
        except IncompleteDataError as exc:
            # Marker-delimited structured error so the daemon can scoop
            # it the same way it scoops the success payload. Exit 3
            # mirrors paper_backtest's hard-block convention.
            sys.stdout.write(f"\n{RESULT_BEGIN}\n")
            sys.stdout.write(json.dumps({
                "kind": "backtest",
                "error": exc.to_dict(),
            }, default=str))
            sys.stdout.write(f"\n{RESULT_END}\n")
            sys.stdout.flush()
            raise SystemExit(3) from exc
        except ValueError as exc:
            # Auto-resolver couldn't classify a symbol — surface the
            # actionable error rather than crashing inside BarStore.
            sys.stdout.write(f"\n{RESULT_BEGIN}\n")
            sys.stdout.write(json.dumps({
                "kind": "backtest",
                "error": {
                    "kind": "unresolved_asset_class",
                    "message": str(exc),
                    "remediation": (
                        "Pass `data_asset` on the payload to force a class, "
                        "or `legacy_cache=true` to skip preflight."
                    ),
                },
            }, default=str))
            sys.stdout.write(f"\n{RESULT_END}\n")
            sys.stdout.flush()
            raise SystemExit(4) from exc

    log.info(
        "loading bars for %d symbols from %s to %s",
        len(symbols), start.date(), end.date(),
    )
    data = _load_sleeve_data(symbols, start, end)
    if not data:
        raise SystemExit("ERROR: no symbols returned bars; cannot run backtest")

    # Thread the entry-discipline gates from the payload so a backtest can A/B
    # the live clone's settings (entry_max_ext_pct / entry_rsi_max / stop_loss_pct).
    # Absent → None → ungated (the trader-verbatim control), so existing runs are
    # unchanged. This is what lets us VALIDATE the clone gate change on history.
    config = QuantEngineConfig(
        initial_capital=initial_capital,
        entry_max_ext_pct=payload.get("entry_max_ext_pct"),
        entry_rsi_max=payload.get("entry_rsi_max"),
        stop_loss_pct=payload.get("stop_loss_pct"),
    )
    sleeve_name = str(payload.get("strategy") or "default_sleeve")
    sleeve = Sleeve(name=sleeve_name, data=data, config=config)
    ensemble = Ensemble(sleeves=[sleeve], config=config, initial_capital=initial_capital)
    log.info("running ensemble with sleeve=%s tickers=%d", sleeve_name, len(data))
    result = ensemble.run()

    # Monte Carlo with sensible defaults; payload can override any.
    mc_cfg = payload.get("monte_carlo") or {}
    n_sims = int(mc_cfg.get("n_sims", 500))
    years = int(mc_cfg.get("years", 5))
    seed = mc_cfg.get("seed")
    seed = int(seed) if seed is not None else None
    log.info("running monte carlo n_sims=%d years=%d seed=%s", n_sims, years, seed)
    mc = MonteCarloSimulator(returns=result.daily_returns, seed=seed).run(
        initial=initial_capital, years=years, n_sims=n_sims,
    )

    spy_equity, spy_summary = _spy_benchmark(start, end, initial_capital)
    return result, mc, spy_equity, spy_summary, preflight_payload


def _build_result_summary(
    payload: dict[str, Any],
    result: EnsembleResult,
    mc: MonteCarloResult,
    spy_equity: Any,
    spy_summary: dict,
    request_id: str | None,
    preflight_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the final result_summary envelope.

    Charts attach at the TOP LEVEL (not per-strategy) because the
    backtest is a single-strategy run by definition — the per-strategy
    nesting in paper sessions exists to disambiguate when multiple
    strategies share one session, which never happens for backtests.
    The frontend's ``extractCharts`` helper picks either shape.
    """
    label = str(payload.get("label") or payload.get("strategy") or "backtest")

    backtest_chart = build_chart(
        "backtest_4panel",
        result=result,
        spy_equity=spy_equity,
        spy_summary=spy_summary,
        title=label,
    )
    mc_chart = build_chart("monte_carlo_fan", mc=mc)

    summary = {
        "label": label,
        "strategy": payload.get("strategy"),
        "symbols": list(payload.get("symbols") or []),
        "start": payload.get("start"),
        "end": payload.get("end"),
        "initial_capital": float(payload.get("initial_capital", 100_000.0)),
        "n_sims": int((payload.get("monte_carlo") or {}).get("n_sims", 500)),
        "years": int((payload.get("monte_carlo") or {}).get("years", 5)),
        "ensemble_summary": result.summary,
        "monte_carlo_summary": mc.summary,
        "spy_summary": spy_summary,
        "final_equity": float(result.equity.iloc[-1]) if len(result.equity) else 0.0,
    }
    if request_id is not None:
        summary["request_id"] = request_id

    # Strategies block mirrors paper-session shape so Session Detail's
    # tabs render uniformly even though backtests have no fills / bars.
    strategies = [{
        "strategy_id": label,
        "equity": summary["final_equity"],
        "realised_pnl": 0.0,
        "unrealised_pnl": 0.0,
        "fills_count": 0,
        "commission_paid": 0.0,
        "decisions": [],
        "bars_seen": [],
        "recent_fills": [],
        "positions": [],
    }]

    envelope: dict[str, Any] = {
        "kind": "backtest",
        "summary": summary,
        "charts": {
            "backtest_4panel": backtest_chart,
            "monte_carlo_fan": mc_chart,
        },
        "strategies": strategies,
    }
    # Phase D-3 / E — when preflight ran, stamp the combined hash at
    # the top level (matches paper_backtest's envelope shape so the
    # .NET ingest path picks it up unchanged) and into summary so
    # consumers reading either layer see it.
    if preflight_payload is not None:
        envelope.update(preflight_payload)
        summary["data_state_hash"] = preflight_payload["data_state_hash"]
    return envelope


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_backtest_from_payload(payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    """Library entry point — used by tests and the daemon in-process path.

    Returns the result_summary dict. JSON-safety is guaranteed by
    ``build_chart`` (which round-trips through plotly.io.to_json),
    but the caller is free to ``json.dumps`` to confirm.
    """
    result, mc, spy_equity, spy_summary, preflight_payload = _run_backtest(payload)
    return _build_result_summary(
        payload, result, mc, spy_equity, spy_summary, request_id,
        preflight_payload=preflight_payload,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    payload = _load_payload(args)
    # CLI flags override payload values for the preflight knobs so
    # operators can re-run an existing payload with stricter / looser
    # data trustworthiness without editing the JSON.
    if args.data_asset is not None:
        payload["data_asset"] = args.data_asset
    if args.data_resolution is not None:
        payload["data_resolution"] = args.data_resolution
    if args.data_api_base is not None:
        payload["data_api_base"] = args.data_api_base
    if args.allow_incomplete_data:
        payload["allow_incomplete_data"] = True
    if args.legacy_cache:
        payload["legacy_cache"] = True
    summary = run_backtest_from_payload(payload, request_id=args.request_id)

    # Marker-delimited so the daemon can scoop the dict out of stdout
    # even if downstream libraries also write to stdout.
    sys.stdout.write(f"\n{RESULT_BEGIN}\n")
    sys.stdout.write(json.dumps(summary, default=str))
    sys.stdout.write(f"\n{RESULT_END}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
