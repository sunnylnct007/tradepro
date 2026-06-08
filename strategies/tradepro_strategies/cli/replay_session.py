"""tradepro-replay-session — replay a historical intraday session.

Runs a strategy (default: intraday_flat) across its full candidate
universe for a given date and produces a per-symbol P&L breakdown.
No orders are sent to any broker — pure simulation driven by yfinance
historical bars.

Primary use-case: "what happened on Friday?" — diagnose large P&L
swings by running the strategy with the same logic + real bar data.

Examples
--------
    # Friday 2026-06-06 replay (the £2K loss day)
    uv run tradepro-replay-session --date 2026-06-06

    # Different strategy
    uv run tradepro-replay-session --date 2026-06-06 --strategy ichimoku_equity

    # Override the candidate list
    uv run tradepro-replay-session --date 2026-06-06 --symbols "AAPL,NVDA,MSFT"

    # Push result to the API for cockpit display
    uv run tradepro-replay-session --date 2026-06-06 --push

Output JSON shape
-----------------
{
  "kind": "session_replay",
  "date": "2026-06-06",
  "strategy": "intraday_flat",
  "total_realised_pnl_usd": -1847.32,
  "total_fills": 47,
  "symbols_with_loss": ["NVDA", "TSLA", "AMD"],
  "symbols_with_gain": ["AAPL", "MSFT"],
  "per_symbol": [
    {
      "symbol": "NVDA",
      "realised_pnl_usd": -612.40,
      "fills": 8,
      "win_trades": 2,
      "loss_trades": 6,
      "max_adverse_excursion_usd": -680.0,
      "decisions": [...]   // only with --verbose
    },
    ...
  ],
  "risk_summary": {
    "worst_symbol": "NVDA",
    "worst_pnl_usd": -612.40,
    "max_single_symbol_loss_usd": -612.40,
    "pnl_std_dev": 243.1
  }
}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from datetime import date, datetime, timezone
from typing import Any

_log = logging.getLogger("tradepro.cli.replay_session")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-replay-session",
        description=(
            "Replay a historical intraday session — runs the strategy "
            "on yfinance bars and produces a per-symbol P&L breakdown. "
            "No orders are sent to any broker."
        ),
    )
    p.add_argument("--date", required=True,
                   help="Session date to replay (YYYY-MM-DD).")
    p.add_argument("--strategy", default="intraday_flat",
                   help="Registered strategy name (default: intraday_flat).")
    p.add_argument("--symbols", default=None,
                   help="Override candidate list (comma-separated). Defaults to "
                        "the strategy's own default candidates.")
    p.add_argument("--interval", default="1m",
                   choices=["1m", "5m", "15m", "30m", "60m"])
    p.add_argument("--risk-per-trade-usd", type=float, default=None,
                   help="Risk per trade override (uses strategy default if unset).")
    p.add_argument("--verbose", action="store_true",
                   help="Include per-symbol decisions in output.")
    p.add_argument("--push", action="store_true",
                   help="POST the result to the API as a replay report.")
    p.add_argument("--log-level", default="WARNING",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def _resolve_candidates(strategy_name: str, symbols_override: str | None) -> list[str]:
    """Get the candidate symbols — from the CLI override or the strategy's
    own default_params."""
    if symbols_override:
        return [s.strip().upper() for s in symbols_override.split(",") if s.strip()]

    from ..paper import registry as strategy_registry
    from ..paper.strategies import intraday_flat  # ensure registered  # noqa: F401
    spec = strategy_registry.get(strategy_name)
    params = spec.default_params()
    return params.get("candidates") or params.get("symbols") or []


async def _run_symbol(
    symbol: str,
    session_date: datetime,
    strategy_name: str,
    interval: str,
    risk_per_trade_usd: float | None,
    verbose: bool,
) -> dict[str, Any]:
    """Run one symbol through the strategy for the replay date.
    Returns a per-symbol result dict."""
    from ..paper import RiskLimits
    from ..paper import registry as strategy_registry
    from ..paper.engine import Engine
    from ..paper.profiles import build_session
    from ..paper.strategies import build as build_strategy  # noqa: F401
    # Ensure all strategies are registered
    import tradepro_strategies.paper.strategies  # noqa: F401

    spec = strategy_registry.get(strategy_name)
    params = dict(spec.default_params())
    if risk_per_trade_usd is not None:
        params["risk_per_trade_usd"] = risk_per_trade_usd

    strategy_id = f"replay-{strategy_name}-{symbol}"
    try:
        strat = spec.build(
            strategy_id=strategy_id,
            params=params,
            risk=RiskLimits(max_position_value_usd=10_000.0, allow_short=False),
        )
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": f"strategy build failed: {exc}",
            "realised_pnl_usd": 0.0,
            "fills": 0,
        }

    try:
        bus, router = build_session(
            broker="yfinance",
            symbols=[symbol],
            session_date=session_date,
            interval=interval,
            pace_seconds=None,
            lookback_days=0,   # replay: only the session day's 1m bars; daily
                               # Ichimoku uses its own separate daily-bar fetch
        )
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": f"session build failed: {exc}",
            "realised_pnl_usd": 0.0,
            "fills": 0,
        }

    engine = Engine(bus=bus, router=router)
    engine.register_strategy(strat, symbols=[symbol])

    try:
        await engine.run(session_date)
    except Exception as exc:
        return {
            "symbol": symbol,
            "error": f"engine.run failed: {exc}",
            "realised_pnl_usd": 0.0,
            "fills": 0,
        }

    snap = engine.ledger.to_snapshot(include_fills=50)
    engine.attach_decisions(snap)
    engine.attach_bars(snap)

    strategies = snap.get("strategies") or []
    entry = next((s for s in strategies if s.get("strategy_id") == strategy_id), {})

    fills = entry.get("recent_fills") or []
    realised = float(entry.get("realised_pnl") or 0.0)
    fill_count = int(entry.get("fills_count") or 0)

    # Trade-level win/loss attribution
    win_trades = sum(1 for f in fills if float(f.get("realised_pnl") or 0) > 0)
    loss_trades = sum(1 for f in fills if float(f.get("realised_pnl") or 0) < 0)

    # Max adverse excursion: worst running P&L seen (rough proxy from fills)
    running = 0.0
    peak = 0.0
    mae = 0.0
    for f in sorted(fills, key=lambda x: x.get("filled_at") or ""):
        running += float(f.get("realised_pnl") or 0)
        peak = max(peak, running)
        mae = min(mae, running - peak)

    result: dict[str, Any] = {
        "symbol": symbol,
        "realised_pnl_usd": round(realised, 2),
        "fills": fill_count,
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "max_adverse_excursion_usd": round(mae, 2),
    }

    if verbose:
        decisions = entry.get("decisions") or []
        result["decisions"] = decisions[:50]   # cap to avoid huge output
        result["bars_count"] = len(entry.get("bars") or [])

    return result


def _risk_summary(per_symbol: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [r["realised_pnl_usd"] for r in per_symbol if "error" not in r]
    if not pnls:
        return {}
    worst = min(per_symbol, key=lambda r: r.get("realised_pnl_usd", 0))
    best = max(per_symbol, key=lambda r: r.get("realised_pnl_usd", 0))
    mean = sum(pnls) / len(pnls)
    variance = sum((x - mean) ** 2 for x in pnls) / len(pnls)
    return {
        "worst_symbol": worst["symbol"],
        "worst_pnl_usd": worst["realised_pnl_usd"],
        "best_symbol": best["symbol"],
        "best_pnl_usd": best["realised_pnl_usd"],
        "max_single_symbol_loss_usd": min(pnls),
        "pnl_std_dev": round(math.sqrt(variance), 2),
        "symbols_traded": len([r for r in per_symbol if r["fills"] > 0]),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    session_date = datetime.fromisoformat(args.date).replace(
        hour=13, minute=30, tzinfo=timezone.utc
    )

    candidates = _resolve_candidates(args.strategy, args.symbols)
    if not candidates:
        raise SystemExit(
            f"No candidates found for strategy '{args.strategy}'. "
            "Pass --symbols explicitly."
        )

    _log.warning("Replaying %s across %d symbols: %s",
                 args.date, len(candidates), ", ".join(candidates))

    per_symbol: list[dict[str, Any]] = []
    for sym in candidates:
        _log.warning("  running %s...", sym)
        result = asyncio.run(_run_symbol(
            symbol=sym,
            session_date=session_date,
            strategy_name=args.strategy,
            interval=args.interval,
            risk_per_trade_usd=args.risk_per_trade_usd,
            verbose=args.verbose,
        ))
        per_symbol.append(result)
        pnl = result.get("realised_pnl_usd", 0)
        fills = result.get("fills", 0)
        err = result.get("error", "")
        _log.warning("    %s: P&L $%.2f  fills=%d%s",
                     sym, pnl, fills, f"  ERROR: {err}" if err else "")

    total_pnl = sum(r.get("realised_pnl_usd", 0) for r in per_symbol)
    total_fills = sum(r.get("fills", 0) for r in per_symbol)

    per_symbol_sorted = sorted(per_symbol, key=lambda r: r.get("realised_pnl_usd", 0))

    output: dict[str, Any] = {
        "kind": "session_replay",
        "date": args.date,
        "strategy": args.strategy,
        "symbols_run": candidates,
        "total_realised_pnl_usd": round(total_pnl, 2),
        "total_fills": total_fills,
        "symbols_with_loss": [
            r["symbol"] for r in per_symbol_sorted
            if r.get("realised_pnl_usd", 0) < 0
        ],
        "symbols_with_gain": [
            r["symbol"] for r in per_symbol_sorted
            if r.get("realised_pnl_usd", 0) > 0
        ],
        "per_symbol": per_symbol_sorted,
        "risk_summary": _risk_summary(per_symbol),
    }

    print(json.dumps(output, indent=2, default=str))

    if args.push:
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        push_to_api.push("session-replay", output, base, token)

    return 0 if total_pnl >= 0 else 1   # non-zero exit on loss for scripting


if __name__ == "__main__":
    raise SystemExit(main())
