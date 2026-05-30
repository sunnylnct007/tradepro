"""tradepro-paper — run one paper-trading session end-to-end.

Picks a broker profile (replay / yfinance / t212 / ibkr / stub_live),
instantiates the engine, registers a strategy, and prints the ledger
snapshot. Designed for "smoke a single session from the terminal".

Strategies
----------
  orb              (default)
    Intraday Opening-Range-Breakout. 1m bars, single symbol.
    Example:
        uv run tradepro-paper --broker yfinance --symbol AAPL --strategy orb

  ichimoku_equity
    Daily Ichimoku trend-following on up to 50 equities.  MOO signal
    fires on the first daily bar.  Fetches own 700-day history via the
    on-disk cache (no extra bar-feed data required beyond triggering).
    Example:
        uv run tradepro-paper \\
            --broker t212 \\
            --strategy ichimoku_equity \\
            --symbols AAPL,MSFT,NVDA,TSLA \\
            --capital-usd 100000 \\
            --sleeve-size 20 \\
            --interval 1d

  ichimoku_fx_mr
    Hourly G10 FX mean-reversion (fade-the-break) across all 10 pairs.
    Warmup = 200 bars; positions are signed (+1/-1/±2/±3 units).
    Example:
        uv run tradepro-paper \\
            --broker t212 \\
            --strategy ichimoku_fx_mr \\
            --symbols EURUSD,GBPUSD,USDJPY \\
            --capital-usd 50000 \\
            --interval 1h

T212 live trading requires both `--allow-real-orders` AND the env
var `TRADEPRO_T212_ALLOW_LIVE=1` — same two-key gate the router enforces.
IBKR live trading needs `TRADEPRO_IBKR_ALLOW_LIVE=1` and a non-DU
account id.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

from ..paper import RiskLimits
from ..paper.engine import Engine
from ..paper.profiles import build_multi_broker_session, build_session
from ..paper.strategies.opening_range_breakout import OpeningRangeBreakout


_STRATEGY_CHOICES = ("orb", "ichimoku_equity", "ichimoku_fx_mr")

# Sensible interval defaults per strategy — overridden by --interval.
_DEFAULT_INTERVALS = {
    "orb": "1m",
    "ichimoku_equity": "1d",
    "ichimoku_fx_mr": "1h",
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-paper",
        description="Run one paper-trading session against a chosen broker.",
    )
    p.add_argument(
        "--broker", required=True,
        help=(
            "Broker profile. Single: replay | yfinance | t212 | ibkr | "
            "stub_live. Multi: comma-separated list (e.g. 't212,ibkr') — "
            "see --multi-mode and --bar-source."
        ),
    )
    p.add_argument(
        "--strategy",
        choices=_STRATEGY_CHOICES,
        default="orb",
        help=(
            "Trading strategy to run. "
            "orb=Opening Range Breakout (intraday, single symbol); "
            "ichimoku_equity=Daily Ichimoku trend-following (multi-symbol, MOO); "
            "ichimoku_fx_mr=Hourly G10 FX mean-reversion (multi-pair). "
            "Default: orb"
        ),
    )
    p.add_argument(
        "--multi-mode", choices=["shadow", "dispatch"], default="shadow",
        help="Only used with a multi-broker --broker list. "
             "shadow=send every order to every broker; dispatch=route by strategy_id.",
    )
    p.add_argument(
        "--bar-source", choices=["yfinance", "ibkr", "replay"], default="yfinance",
        help="Bar feed used with multi-broker mode (single-broker mode derives this from --broker).",
    )
    # ── Symbol args ──────────────────────────────────────────────────────
    # --symbol: legacy single-symbol (backward-compat for orb)
    # --symbols: comma-separated, preferred for ichimoku_equity / ichimoku_fx_mr
    p.add_argument(
        "--symbol", default=None,
        help="Single symbol (e.g. AAPL). For multi-symbol strategies prefer --symbols.",
    )
    p.add_argument(
        "--symbols", default=None,
        help=(
            "Comma-separated symbols/pairs (e.g. AAPL,MSFT,NVDA). "
            "For ichimoku_fx_mr defaults to all 10 G10 pairs if omitted."
        ),
    )
    p.add_argument(
        "--date", default=None,
        help="Session date YYYY-MM-DD (required for replay/yfinance/t212/stub_live)",
    )
    p.add_argument("--strategy-id", default=None,
                   help="strategy_id stamped onto orders + ledger book "
                        "(defaults to the strategy name).")
    p.add_argument("--capital-usd", type=float, default=100_000.0,
                   help="Sub-account capital (total) used by risk + sizing.")
    # ── ORB knobs ────────────────────────────────────────────────────────
    p.add_argument("--max-position-value-usd", type=float, default=10_000.0,
                   help="[orb] Hard cap on |position_value| in dollars.")
    p.add_argument("--risk-per-trade-usd", type=float, default=100.0,
                   help="[orb] Dollars risked on the stop.")
    p.add_argument("--range-minutes", type=int, default=15,
                   help="[orb] Opening-range window length (minutes).")
    # ── Ichimoku equity knobs ────────────────────────────────────────────
    p.add_argument("--sleeve-size", type=int, default=20,
                   help="[ichimoku_equity] Max concurrent positions in the sleeve.")
    p.add_argument("--target-vol", type=float, default=0.12,
                   help="[ichimoku_equity/fx_mr] Annual vol target for sizing (default 0.12).")
    p.add_argument("--max-leverage", type=float, default=1.5,
                   help="[ichimoku_equity] Max leverage scalar (default 1.5).")
    p.add_argument("--no-regime-filter", action="store_true",
                   help="[ichimoku_equity] Disable the SPY 200-SMA regime gate.")
    # ── Ichimoku FX knobs ────────────────────────────────────────────────
    p.add_argument("--warmup-bars", type=int, default=200,
                   help="[ichimoku_fx_mr] Bars of history before signals fire.")
    # ── Shared bar knobs ─────────────────────────────────────────────────
    p.add_argument("--interval", default=None,
                   help="Yfinance interval (1m/5m/15m/1h/1d). "
                        "Defaults: orb→1m, ichimoku_equity→1d, ichimoku_fx_mr→1h.")
    p.add_argument("--pace-seconds", default=None,
                   help="Replay pace: float seconds, 'realtime', or omit for "
                        "as-fast-as-possible.")
    # ── T212 knobs ───────────────────────────────────────────────────────
    p.add_argument("--t212-mode", choices=["demo", "live"], default="demo")
    p.add_argument("--allow-real-orders", action="store_true",
                   help="Live trading opt-in (must also set the corresponding env var).")
    p.add_argument("--placement-mode", choices=["auto", "manual"], default=None,
                   help="auto=strategy posts to T212 directly. "
                        "manual=push to pending queue for human Approve/Reject. "
                        "Omitted=read from /api/settings, fall back to 'manual'.")
    # ── IBKR knobs ───────────────────────────────────────────────────────
    p.add_argument("--account", default=None,
                   help="IBKR account id (DU...=paper, U...=live).")
    p.add_argument("--ibkr-timeframe-seconds", type=int, default=60)
    # ── Push knobs ───────────────────────────────────────────────────────
    p.add_argument("--push", action="store_true",
                   help="POST the ledger snapshot to the API after the session "
                        "so the Paper page Live tab can render it.")
    p.add_argument("--push-fills", type=int, default=50,
                   help="Most-recent fills per strategy to include in the push "
                        "(default 50). 0=positions/aggregates only.")
    p.add_argument("--lookback-days", type=int, default=0,
                   help="Extend the Yahoo bar fetch backwards from --date by N "
                        "days so warmup-hungry strategies (ichimoku_fx_mr needs "
                        "~107 days for 1h bars) can satisfy their gate. 0=session "
                        "date only (default; correct for ma_crossover/ORB).")
    return p.parse_args(argv)


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    """Merge --symbol and --symbols into a deduplicated list."""
    out: list[str] = []
    if args.symbols:
        out.extend(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    if args.symbol:
        sym = args.symbol.strip().upper()
        if sym not in out:
            out.append(sym)
    if not out:
        if args.strategy in ("ichimoku_fx_mr",):
            # Default to all G10 pairs.
            from ..quant_engine.fx_strategy import G10_PAIRS
            out = list(G10_PAIRS.keys())
        else:
            raise SystemExit(
                "ERROR: at least one symbol is required. "
                "Use --symbol AAPL or --symbols AAPL,MSFT,NVDA"
            )
    return out


def _resolve_session_date(arg: str | None) -> datetime | None:
    if arg is None:
        return None
    return datetime.fromisoformat(arg)


def _resolve_pace(arg: str | None) -> float | str | None:
    if arg is None:
        return None
    if arg == "realtime":
        return "realtime"
    return float(arg)


def _build_strategy(args: argparse.Namespace, symbols: list[str]):
    """Construct the chosen strategy object."""
    strategy_name = args.strategy
    strategy_id = args.strategy_id or strategy_name

    if strategy_name == "orb":
        return OpeningRangeBreakout(
            strategy_id=strategy_id,
            params={
                "range_minutes": args.range_minutes,
                "risk_per_trade_usd": args.risk_per_trade_usd,
            },
            risk=RiskLimits(
                max_position_value_usd=args.max_position_value_usd,
                allow_short=False,
            ),
        )

    if strategy_name == "ichimoku_equity":
        from ..paper.strategies.ichimoku_equity import IchimokuEquityStrategy
        return IchimokuEquityStrategy(
            strategy_id=strategy_id,
            params={
                "symbols": symbols,
                "capital_usd": args.capital_usd,
                "sleeve_size": args.sleeve_size,
                "target_vol": args.target_vol,
                "max_leverage": args.max_leverage,
                "use_regime_filter": not args.no_regime_filter,
            },
        )

    if strategy_name == "ichimoku_fx_mr":
        from ..paper.strategies.ichimoku_fx_mr import IchimokuFXMeanReversionStrategy
        return IchimokuFXMeanReversionStrategy(
            strategy_id=strategy_id,
            params={
                "pairs": symbols,
                "capital_usd": args.capital_usd,
                "vol_target": args.target_vol,
                "warmup_bars": args.warmup_bars,
            },
            # FX trades both directions by design — pairs are symmetric
            # (long EURUSD = short USDEUR). allow_short=True so the
            # risk gate doesn't reject sell-to-flat-or-short orders the
            # strategy emits when the cloud flips bearish.
            risk=RiskLimits(
                max_position_value_usd=args.max_position_value_usd,
                allow_short=True,
            ),
        )

    raise ValueError(f"Unknown strategy {strategy_name!r}")


class PositionSeedError(RuntimeError):
    """The strategy's current position could NOT be confirmed from the
    broker (the golden source).

    A `--push` session against a real broker MUST abort on this rather
    than fall back to an assumed-flat book: starting flat when a position
    is actually open makes the strategy recompute a full entry every run
    and stack duplicate orders at the broker. Fail-closed is the only
    safe posture on an execution path. See main()'s seeding block.
    """


# Sim/paper brokers hold no position that persists across runs, so a
# re-run cannot double a real position — seeding is not required and a
# seed failure is harmless. EVERYTHING ELSE is treated as a real broker:
# the broker is the golden source of position (OMS is audit-only) and a
# --push session MUST confirm the current position from it before
# emitting orders. Listing only the sim brokers here makes this
# fail-closed BY DEFAULT — a new broker added later is treated as real
# until proven otherwise, so it can never silently trade on a guessed
# flat book.
_SIM_BROKERS = frozenset({"paper", "replay", "yfinance", "stub_live"})

# Per-broker positions endpoint (the golden source). A real broker that
# is MISSING here is a hard error under --push: we can't confirm the
# position, so we must not trade. Wire a new broker's endpoint here when
# it gains live execution.
_REAL_BROKER_POSITION_PATHS = {
    "t212": "/api/integrations/trading212/positions?account=demo",
    "ig":   "/api/integrations/ig/positions",
}


def broker_requires_position_seed(broker: str) -> bool:
    """True when `broker` holds positions across runs and must therefore
    be seeded from before trading (fail-closed by default for any broker
    not explicitly known to be a sim)."""
    return broker.strip().lower() not in _SIM_BROKERS


def _seed_strategy_positions_from_broker(strategy, broker: str) -> dict[str, int]:
    """Fetch the strategy's current position FROM THE BROKER and seed it
    so reruns compute a delta (target - current) instead of re-emitting a
    full entry every run.

    The broker — NOT the OMS — is authoritative. OMS is audit-only and
    can drift (fills recorded without matching rows, or broker-side
    activity outside the system). The user has stated this repeatedly:
    "ensure the position is always sourced from source and not OMS as
    they might be out of sync" / "the IG broker portal should be the
    golden source of position" / "anytime we run a strategy the position
    should be taken from broker". See memory: broker_is_golden_source.

    Raises PositionSeedError if the position cannot be CONFIRMED for this
    broker — missing seed hook, unknown broker positions endpoint,
    network/HTTP failure, or an unparseable response. Callers running
    with --push MUST abort on this rather than trade blind.

    Returns {symbol: signed_int_qty}. An EMPTY dict is a positive result:
    the broker confirmed a genuinely flat book, so opening new positions
    is safe. (A failure to reach the broker is NOT an empty dict — it
    raises.)
    """
    b = broker.strip().lower()
    if not hasattr(strategy, "seed_positions"):
        raise PositionSeedError(
            f"strategy {getattr(strategy, 'strategy_id', '?')!r} has no "
            f"seed_positions hook — cannot confirm position from {b!r}"
        )
    path = _REAL_BROKER_POSITION_PATHS.get(b)
    if path is None:
        raise PositionSeedError(
            f"no positions endpoint wired for broker {b!r} — refusing to "
            f"trade without confirming the current position from the "
            f"golden source. Add it to _REAL_BROKER_POSITION_PATHS."
        )
    log = logging.getLogger("tradepro.cli")
    try:
        import requests
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        base = base.rstrip('/')
        resp = requests.get(f"{base}{path}", headers=headers, timeout=10)
        resp.raise_for_status()
        rows = resp.json().get("positions") or []
    except Exception as exc:  # noqa: BLE001
        # Any failure to READ the broker is fail-closed — do NOT fall
        # back to flat, do NOT fall back to OMS. Raise so the caller
        # aborts the session.
        raise PositionSeedError(
            f"could not read {b!r} positions (golden source): {exc}"
        ) from exc

    positions: dict[str, int] = {}
    for r in rows:
        t = (r.get("ticker") or r.get("epic") or "").upper()
        if not t:
            continue
        # Strip broker suffixes so the strategy's internal book
        # (which keys on bare ticker / pair) finds a match:
        #   AAPL_US_EQ              → AAPL
        #   CS.D.EURUSD.MINI.IP     → EURUSD
        #   CS.D.GBPUSD.CFD.IP      → GBPUSD
        # IG epic format is fixed: <market_class>.D.<pair>.<size>.IP
        bare = t
        if t.startswith("CS.D.") or t.startswith("IX.D."):
            parts = t.split(".")
            if len(parts) >= 4:
                bare = parts[2]
        elif "_" in t:
            bare = t.split("_", 1)[0]
        try:
            # Truncate toward zero so we never overstate the held
            # quantity — T212 fractional shares (6.7022 NVDA) rounding
            # up would trigger "selling more than owned" rejections.
            # NOTE: IG MINI FX positions report in mini-lots (often
            # < 1.0), which this truncates to 0 — a known follow-up
            # (symmetric units<->mini-lot conversion) tracked separately.
            qty = int(float(r.get("quantity") or 0))
        except (TypeError, ValueError):
            continue
        if qty != 0:
            positions[bare] = positions.get(bare, 0) + qty

    if positions:
        log.info(
            "POSITION SEED (%s-broker): %s starting with %s",
            b, strategy.strategy_id, positions,
        )
        strategy.seed_positions(positions)
    else:
        log.info(
            "POSITION SEED (%s-broker): %s — broker confirms a flat book",
            b, strategy.strategy_id,
        )
    return positions


def _abort_on_unconfirmed_position(
    log, *, strategy_id: str, strategy_name: str, broker: str,
    symbols: list[str], reason: str,
) -> None:
    """Loudly log the fail-closed abort AND surface it to the UI via
    /api/ingest/alert so a silent broker timeout doesn't hide a strategy
    that has stopped trading. Best-effort on the API post — the local
    log line is the source of truth either way."""
    msg = (
        f"ABORTED {strategy_name} session: could not confirm the current "
        f"position from broker {broker!r} (the golden source) — {reason}. "
        f"NO orders were emitted (fail-closed) to avoid stacking duplicate "
        f"orders on an assumed-flat book. The strategy resumes "
        f"automatically once the broker positions endpoint is reachable."
    )
    log.error("POSITION SEED FAILED — %s", msg)
    try:
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        push_to_api.raise_alert(
            base, token,
            source="paper-session",
            severity="critical",
            code="position_seed_failed",
            title=f"{strategy_name} aborted — broker position unconfirmed",
            detail=msg,
            strategy_id=strategy_id,
            broker=broker,
            symbols=symbols,
            # One open alert per (strategy, broker): repeated failures
            # refresh the same row instead of flooding the UI.
            dedup_key=f"position_seed_failed:{strategy_id}:{broker}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("could not post position-seed alert to API: %s", exc)


def _fetch_oms_positions(url: str, params: dict, headers: dict) -> dict[str, int]:
    """Helper: GET /api/oms/positions → {symbol: signed_int_qty}.
    Returns {} on any failure or empty result."""
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        rows = resp.json().get("positions") or []
        out: dict[str, int] = {}
        for r in rows:
            sym = (r.get("symbol") or "").upper()
            if not sym:
                continue
            bare = sym.split("_", 1)[0]
            try:
                qty = int(round(float(r.get("quantity") or 0)))
            except (TypeError, ValueError):
                continue
            if qty != 0:
                out[bare] = out.get(bare, 0) + qty
        return out
    except Exception:  # noqa: BLE001
        return {}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("tradepro.cli")
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    session_date = _resolve_session_date(args.date)
    # Yahoo / T212 / IG profiles require a session_date — when omitted
    # default to today's UTC date so the schedule-fired daemons
    # (paper-equity, paper-fx) Just Work without a --date flag.
    if session_date is None:
        session_date = datetime.utcnow().replace(microsecond=0)
        log.info("session_date defaulted to today UTC: %s", session_date.date())
    symbols = _resolve_symbols(args)

    # Interval: CLI flag → strategy default.
    if args.interval is None:
        args.interval = _DEFAULT_INTERVALS.get(args.strategy, "1m")
        log.info("interval defaulted to %s for strategy %s", args.interval, args.strategy)

    # Placement-mode resolution: explicit CLI flag wins, else fetch
    # the user's UI-set value from /api/settings, else fall back to
    # the conservative default (manual = human-in-the-loop, no
    # surprise live orders).
    if args.placement_mode is None:
        from ..api_settings import get_placement_mode
        api_mode = get_placement_mode()
        resolved_placement_mode = api_mode or "manual"
        log.info(
            "placement-mode resolved to %s (source=%s)",
            resolved_placement_mode,
            "api-settings" if api_mode else "default",
        )
    else:
        resolved_placement_mode = args.placement_mode
        log.info("placement-mode = %s (source=cli flag)", resolved_placement_mode)
    args.placement_mode = resolved_placement_mode

    broker_list = [b.strip() for b in args.broker.split(",") if b.strip()]

    # For daily / multi-symbol strategies the bar bus only needs to
    # deliver one trigger bar per symbol — the strategy fetches its own
    # history.  Use the first symbol as the bus anchor; the strategy's
    # on_bar handles the full list itself.
    bus_symbols = symbols if len(symbols) <= 5 else symbols[:5]

    if len(broker_list) > 1:
        bus, router = build_multi_broker_session(
            brokers=broker_list,
            symbols=bus_symbols,
            mode=args.multi_mode,
            bar_source=args.bar_source,
            session_date=session_date,
            interval=args.interval,
            pace_seconds=_resolve_pace(args.pace_seconds),
            t212_mode=args.t212_mode,
            t212_allow_real_orders=args.allow_real_orders,
            t212_placement_mode=args.placement_mode,
            ibkr_default_account=args.account,
            ibkr_allow_real_orders=args.allow_real_orders,
        )
    else:
        bus, router = build_session(
            broker=broker_list[0],
            symbols=bus_symbols,
            session_date=session_date,
            interval=args.interval,
            pace_seconds=_resolve_pace(args.pace_seconds),
            t212_mode=args.t212_mode,
            t212_allow_real_orders=args.allow_real_orders,
            t212_placement_mode=args.placement_mode,
            ibkr_default_account=args.account,
            ibkr_allow_real_orders=args.allow_real_orders,
            ibkr_timeframe_seconds=args.ibkr_timeframe_seconds,
            lookback_days=args.lookback_days,
        )

    strategy = _build_strategy(args, symbols)

    # Seed the strategy with its current position FROM THE BROKER (the
    # golden source) so reruns compute a delta (target - current) instead
    # of re-emitting a full entry every run. This is FAIL-CLOSED for any
    # real broker: if the position cannot be confirmed, the session
    # aborts with NO orders emitted — never a flat-start fallback — so we
    # can't stack duplicate orders on an assumed-flat book. Applies to
    # every strategy and every (non-sim) broker, current or future.
    # See PositionSeedError / broker_requires_position_seed.
    seeded_positions: dict[str, int] = {}
    if args.push:
        for b in broker_list:
            if not broker_requires_position_seed(b):
                continue  # sim broker — no persistent position to confirm
            try:
                seeded = _seed_strategy_positions_from_broker(strategy, broker=b)
            except PositionSeedError as exc:
                _abort_on_unconfirmed_position(
                    log,
                    strategy_id=strategy.strategy_id,
                    strategy_name=args.strategy,
                    broker=b,
                    symbols=symbols,
                    reason=str(exc),
                )
                return 2  # fail-closed: engine never runs, no orders
            seeded_positions.update(seeded)

    engine = Engine(bus=bus, router=router)
    engine.register_strategy(
        strategy, symbols=symbols, capital_usd=args.capital_usd,
    )

    # Also seed the engine ledger so its risk gate sees the same
    # world the strategy does. Without this, the strategy emits
    # SELL on a held long, the engine ledger thinks position=0, the
    # gate rejects "would extend short" → SELL never reaches the
    # router. project_broker_is_golden_source: broker is truth, both
    # strategy and engine must reflect that.
    if seeded_positions and hasattr(engine, "ledger"):
        engine.ledger.seed_positions(strategy.strategy_id, seeded_positions)
        log.info(
            "LEDGER SEED: %s engine.ledger.book mirrored %d position(s)",
            strategy.strategy_id, len(seeded_positions),
        )

    log.info(
        "Starting %s session: strategy=%s symbols=%s broker=%s interval=%s",
        args.strategy, strategy.strategy_id, symbols, args.broker, args.interval,
    )
    asyncio.run(engine.run(session_date or datetime.utcnow()))

    # Re-snapshot with recent fills so the Paper page Live tab renders
    # the per-strategy fill log + open positions.
    snapshot = engine.ledger.to_snapshot(include_fills=args.push_fills)
    # Re-apply decisions / bars_seen / charts: ledger.to_snapshot doesn't
    # know about strategy instances, so the engine owns these side-
    # channels. attach_charts was missing here previously which is why
    # the cockpit's Strategy charts widget never populated after a
    # paper-session push — the engine ran recent_charts() inside its
    # own run() but those got dropped on this re-snapshot.
    engine.attach_decisions(snapshot)
    engine.attach_bars(snapshot)
    engine.attach_charts(snapshot)
    snapshot["kind"] = "paper-snapshot"
    snapshot["session_label"] = (
        f"{args.strategy}-{(session_date or datetime.utcnow()).date().isoformat()}"
    )
    snapshot["broker"] = args.broker
    snapshot["symbols"] = symbols
    print(json.dumps(snapshot, indent=2, default=str))

    if args.push:
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        push_to_api.push("paper-snapshot", snapshot, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
