"""Compare strategies × symbols and emit a ranked JSON payload.

Examples:

    # Rank all strategies on every UK-listed core ETF
    uv run tradepro-compare --watchlist etf_uk_core --from 2010-01-01 \
        --out ../out/etf_uk_core.json

    # Pick the strategies and the rank metric explicitly
    uv run tradepro-compare --symbols VOO,QQQ,VTI \
        --strategies buy_and_hold,sma_crossover,macd_signal_cross \
        --rank cagr_pct

    # Same call, then push the JSON to the API
    uv run tradepro-compare --watchlist etf_us_core --push
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .. import runstate
from ..backtest import FeeModel
from ..compare import CompareConfig, StrategySpec, compare
from ..observability import RunLogger
from ..strategies import available
from ..watchlists import WATCHLISTS, resolve as resolve_watchlist
from . import heartbeat
from .push_to_api import load_credentials, push

DEFAULT_STRATEGIES = ["buy_and_hold", "sma_crossover", "rsi_mean_reversion",
                      "macd_signal_cross", "donchian_breakout", "ichimoku_cloud"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--watchlist", choices=sorted(WATCHLISTS.keys()),
                     help="named universe from watchlists.py")
    src.add_argument("--symbols", help="comma-separated tickers, e.g. VOO,QQQ,VTI")

    p.add_argument("--strategies",
                   default=",".join(DEFAULT_STRATEGIES),
                   help=f"comma-separated, from: {available()}")
    p.add_argument("--provider", default="yahoo",
                   choices=["yahoo", "stooq", "binance"])
    p.add_argument("--from", dest="start", default="2010-01-01")
    p.add_argument("--to", dest="end",
                   default=datetime.utcnow().strftime("%Y-%m-%d"))
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--currency", default="GBP")
    p.add_argument(
        "--stamp-duty",
        default="auto",
        help=(
            "SDRT rate. Default 'auto': 0%% for UCITS ETFs, 0.5%% for LSE "
            "shares, 0%% for non-UK. Pass a float (e.g. 0.005) to force a "
            "flat rate across every symbol in the run."
        ),
    )
    p.add_argument("--commission", type=float, default=0.0)
    p.add_argument("--rank", default="sharpe",
                   choices=["sharpe", "cagr_pct", "total_return_pct",
                            "max_drawdown_pct"])
    p.add_argument("--out", type=Path, default=None,
                   help="write result JSON to path (default: artefact dir)")
    p.add_argument("--push", action="store_true",
                   help="push the JSON to /api/ingest/compare after writing")
    return p.parse_args()


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.watchlist:
        return resolve_watchlist(args.watchlist)
    return [s.strip() for s in args.symbols.split(",") if s.strip()]


def _resolve_strategies(args: argparse.Namespace) -> list[StrategySpec]:
    names = [s.strip() for s in args.strategies.split(",") if s.strip()]
    valid = set(available())
    bad = [n for n in names if n not in valid]
    if bad:
        raise SystemExit(f"unknown strategies: {bad}. Available: {sorted(valid)}")
    return [StrategySpec(name=n) for n in names]


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    symbols = _resolve_symbols(args)
    strategies = _resolve_strategies(args)

    logger = RunLogger()
    logger.emit("compare.start",
                source=args.watchlist or "explicit",
                symbols=symbols,
                strategies=[s.name for s in strategies],
                start=start, end=end, rank=args.rank)

    # Stamp-duty: "auto" (default) → per-symbol via fees.py; a numeric
    # override forces a flat rate across the basket.
    stamp_duty_auto = isinstance(args.stamp_duty, str) and args.stamp_duty == "auto"
    if stamp_duty_auto:
        from ..fees import stamp_duty_summary
        summary = stamp_duty_summary(symbols)
        groups_desc = ", ".join(
            f"{g['count']} @ {g['rate_pct']:.1f}%" for g in summary["groups"]
        ) or "—"
        print(f"Stamp duty: auto ({groups_desc})")
        flat_rate = 0.0  # not used when auto=True; kept for fees.commission only
    else:
        try:
            flat_rate = float(args.stamp_duty)
        except (TypeError, ValueError) as e:
            raise SystemExit(f"--stamp-duty: expected 'auto' or a float, got {args.stamp_duty!r}") from e
        print(f"Stamp duty: flat {flat_rate * 100:.2f}% (override; auto-detection bypassed)")

    # LLM preflight — run BEFORE the slow backtest so a missing
    # Ollama model fails fast with a clear banner, not silently as
    # null sentiment columns 60 seconds later.
    try:
        from ..llm.ollama_provider import OllamaProvider
        llm_health = OllamaProvider().health_summary()
        if llm_health["ok"]:
            print(f"LLM:        {llm_health['message']}")
        else:
            print(f"⚠ LLM:      {llm_health['message']}")
            print(f"            Sentiment scoring will be skipped this run.")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ LLM:      preflight check failed: {e}")

    # Per-universe provider override. Some baskets (e.g.
    # energy_commodities) may want a non-Yahoo source — and once we
    # add Alpha Vantage / IBKR / ICE Endex, the universe is the right
    # place to pin that. Today the registered overrides are all
    # `yahoo` so this is a no-op; the mechanism just makes the eventual
    # swap a one-line config change.
    from ..watchlists import meta_for as _watchlist_meta_for
    universe_provider = _watchlist_meta_for(args.watchlist or "").get(
        "provider", args.provider,
    )
    if universe_provider != args.provider:
        print(f"Provider:   {universe_provider} (from watchlist meta)")

    cfg = CompareConfig(
        provider=universe_provider,
        initial_capital=args.capital,
        currency=args.currency,
        rank_metric=args.rank,
        fees=FeeModel(commission_per_trade=args.commission,
                      stamp_duty_rate=flat_rate),
        stamp_duty_auto=stamp_duty_auto,
    )

    # Mark the Mac as 'currently processing X' so the UI can render a
    # live status badge instead of just last-seen time. Heartbeat at
    # start so the UI updates within seconds; heartbeat at end so the
    # last_refresh stats are captured immediately on completion.
    universe_label = args.watchlist or "custom"
    detail = f"{universe_label} ({len(symbols)} symbols × {len(strategies)} strategies)"
    runstate.write(
        task="compare",
        detail=detail,
        phase="starting",
        run_id=logger.run_id,
    )
    heartbeat.send()
    try:
        runstate.update_phase("backtesting")
        # Pass the logger through so every fetch + scoring boundary
        # gets a structured event in ~/.tradepro/logs/<date>/<run_id>.jsonl.
        payload = compare(symbols, strategies, start, end, cfg, logger=logger)
        payload["universe"] = universe_label
        payload["run_id"] = logger.run_id

        out_path = args.out or (logger.artefact_dir / "compare.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, default=str, indent=2))

        logger.emit("compare.done",
                    rows=len(payload["rows"]),
                    out=str(out_path))

        _print_summary(payload)
        print()
        print(f"run_id:     {logger.run_id}")
        print(f"wrote:      {out_path}")
        print(f"artefacts:  {logger.artefact_dir}")
    finally:
        runstate.clear()
        heartbeat.send()

    if args.push:
        base, token = load_credentials()
        push("compare", payload, base, token)


def _print_summary(payload: dict) -> None:
    rank_metric = payload["rank_metric"]
    print(f"universe={payload.get('universe')}  "
          f"window={payload['from']}..{payload['to']}  "
          f"rank_by={rank_metric}")
    print()
    print(f"{'#':>3} {'symbol':10s} {'strategy':22s} "
          f"{'cagr%':>8s} {'sharpe':>7s} {'maxDD%':>8s} "
          f"{'off52w%':>8s} {'rsi':>5s} {'now':>6s}")
    for row in payload["rows"]:
        stats = row.get("stats") or {}
        ms = row.get("market_state") or {}
        print(f"{row.get('rank', 0):>3} "
              f"{row['symbol']:10s} "
              f"{row['strategy_label'][:22]:22s} "
              f"{_fmt(stats.get('cagr_pct')):>8s} "
              f"{_fmt(stats.get('sharpe')):>7s} "
              f"{_fmt(stats.get('max_drawdown_pct')):>8s} "
              f"{_fmt(ms.get('pct_off_52w_high_pct')):>8s} "
              f"{_fmt(ms.get('rsi_14')):>5s} "
              f"{ms.get('entry_signal', '—'):>6s}")
    bo = payload.get("best_overall")
    if bo:
        # Find the full row for the best pick so we can print its
        # entry-quality verdict alongside the rank.
        best_row = next(
            (r for r in payload["rows"]
             if r["symbol"] == bo["symbol"] and r["strategy"] == bo["strategy"]),
            None,
        )
        ms = (best_row or {}).get("market_state") or {}
        print()
        print(f"best:       {bo['symbol']} via {bo['strategy']} "
              f"({rank_metric}={_fmt(bo.get('value'))})")
        if ms:
            print(f"timing:     {ms.get('entry_signal', '—')} — "
                  f"{ms.get('entry_reason', '')}")


def _fmt(x) -> str:
    if x is None:
        return "—"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return f"{f:,.2f}"


if __name__ == "__main__":
    main()
