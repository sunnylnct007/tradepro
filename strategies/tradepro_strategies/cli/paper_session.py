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
import os
import sys
from datetime import datetime

from ..paper import RiskLimits
from ..paper.engine import Engine
from ..ticker_renames import canonical_ticker
from ..paper.profiles import build_multi_broker_session, build_session
from ..paper.strategies.opening_range_breakout import OpeningRangeBreakout


_STRATEGY_CHOICES = ("orb", "ichimoku_equity", "ichimoku_fx_mr", "intraday_flat")

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
        # Optional at parse time so --from-config can supply it from the
        # config row; main() enforces it after config is applied.
        "--broker", required=False, default=None,
        help=(
            "Broker profile. Single: replay | yfinance | t212 | ibkr | "
            "stub_live. Multi: comma-separated list (e.g. 't212,ibkr') — "
            "see --multi-mode and --bar-source. Required unless --from-config "
            "supplies it."
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
        "--universe", default=None,
        help=(
            "Named universe(s) to trade, comma-separated (e.g. 'high_beta' or "
            "'large_50,high_beta'). Symbols load live from /api/universes/<name> "
            "(effective tickers after overrides) instead of a hardcoded "
            "--symbols list. Merged with --symbols if both are given."
        ),
    )
    p.add_argument(
        "--sleeves", default=None,
        help=(
            "Trader's MULTI-SLEEVE equity sizing: comma-separated "
            "'<universe-or-symbol>:<sleeve_size>' (e.g. "
            "'large_50:20,high_beta:30,GLD:1'). Capital is split EQUALLY across "
            "sleeves; each name is sized by its sleeve's 1/sleeve_size slot "
            "(per-sleeve vol-targeted sizing — matches docs/main 4.py). The "
            "union of sleeve symbols is the universe. Overrides --universe."
        ),
    )
    p.add_argument(
        "--date", default=None,
        help="Session date YYYY-MM-DD (required for replay/yfinance/t212/stub_live)",
    )
    p.add_argument("--strategy-id", default=None,
                   help="strategy_id stamped onto orders + ledger book "
                        "(defaults to the strategy name).")
    p.add_argument("--from-config", action="store_true",
                   help="Load broker/account/strategy + risk params + IBKR "
                        "connection from strategy_broker_map.runtime_config (the "
                        "single source of truth) instead of CLI flags. Requires "
                        "--strategy-id. Same command runs on Mac/AWS/prod.")
    p.add_argument("--capital-usd", type=float, default=100_000.0,
                   help="Sub-account capital (total) used by risk + sizing.")
    # ── ORB knobs ────────────────────────────────────────────────────────
    p.add_argument("--max-position-value-usd", type=float, default=10_000.0,
                   help="[orb] Hard cap on |position_value| in dollars.")
    p.add_argument("--risk-per-trade-usd", type=float, default=100.0,
                   help="[orb] Dollars risked on the stop.")
    p.add_argument("--top-n", type=int, default=None,
                   help="[intraday_flat] Basket size (default 6). Lower = fewer, stronger trades.")
    p.add_argument("--min-atr-pct", type=float, default=None,
                   help="[intraday_flat] Cost floor: skip names with ATR(14)/price below this (e.g. 0.012).")
    p.add_argument("--min-strength", type=float, default=None,
                   help="[intraday_flat] Conviction floor: skip setups scoring below this strength.")
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
    # ── Ichimoku equity RISK controls (default OFF → T212 control unchanged;
    #    switch ON for the protected IBKR-paper clone) ─────────────────────
    p.add_argument("--stop-loss-pct", type=float, default=None,
                   help="[ichimoku_equity] Hard stop: flatten a held long down ≥ this %% (e.g. 8). Off by default.")
    p.add_argument("--take-profit-pct", type=float, default=None,
                   help="[ichimoku_equity] Take-profit: flatten a held long up ≥ this %%. Off by default.")
    p.add_argument("--max-per-sector", type=int, default=None,
                   help="[ichimoku_equity] Concentration cap: max NEW entries per sector. Off by default.")
    p.add_argument("--entry-max-ext-pct", type=float, default=None,
                   help="[ichimoku_equity] Don't-chase gate: skip a NEW long >this %% above its 200-SMA (e.g. 50). Off by default.")
    p.add_argument("--entry-rsi-max", type=float, default=None,
                   help="[ichimoku_equity] Don't-chase gate: skip a NEW long with RSI(14) > this (e.g. 75). Off by default.")
    p.add_argument("--entry-require-above-200sma", action="store_true", default=False,
                   help="[ichimoku_equity clone] Primary-trend floor: skip a NEW long BELOW its own 200-SMA (the TSLA-below-200d case). Deviation from spec → clone only. Off by default.")
    p.add_argument("--entry-veto-ma-suspect", action="store_true", default=False,
                   help="[ichimoku_equity clone] Pending-M&A veto: skip a NEW long on a DEAL-PINNED name (big 12m run + collapsed vol + pinned near high — the WBD case). Deviation from spec → clone only. Off by default.")
    # ── Cross-desk RISK GATE kill-switches (config-driven; ALL off by default →
    #    None → no halt → zero behaviour change until set in runtime_config) ────
    p.add_argument("--max-daily-loss-usd", type=float, default=None,
                   help="[risk] Halt the desk for the day when realised+unrealised P&L drops below -this. Off by default.")
    p.add_argument("--max-drawdown-pct", type=float, default=None,
                   help="[risk] Halt the desk when equity falls this %% from its peak (e.g. 5). Off by default.")
    p.add_argument("--max-open-positions", type=int, default=None,
                   help="[risk] Reject NEW entries beyond this many concurrent positions. Off by default.")
    p.add_argument("--max-position-pct-of-capital", type=float, default=None,
                   help="[risk] Reject a position exceeding this %% of allocated capital (e.g. 15). Off by default.")
    p.add_argument("--exclude-symbols", default=None,
                   help="[risk] Comma-separated symbols this desk must never open/extend (e.g. TSLA,GME). Flatten still allowed. Off by default.")
    p.add_argument("--reconcile-entries", action="store_true", default=False,
                   help="[equity] Widen signal reconciliation from held→universe so a flat "
                        "name whose signal says LONG still BUYS even if the feed delivered no "
                        "trigger bar (fixes missed buys). OPENS positions — off by default; "
                        "enable after reviewing a dry run. Held-EXIT reconciliation is always on.")
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


def _fetch_universe_symbols(name: str) -> list[str]:
    """Load a named universe's EFFECTIVE tickers from the API (the same
    universe the UI shows and tradepro-build-high-beta-universe pushes). The
    daemon reads this live so the trader's dynamic high-beta sleeve drives the
    live book instead of a hardcoded --symbols list. Fail-LOUD: a universe
    that can't be read raises (a silent empty universe = the strategy trades
    nothing / falls back to a stale list, which we must not do quietly)."""
    import requests
    from . import push_to_api
    base, token = push_to_api.load_credentials()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{base.rstrip('/')}/api/universes/{name}"
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    syms = resp.json().get("symbols") or []
    # `effective` reflects INCLUDE/EXCLUDE overrides; only trade what's in.
    out = [str(s.get("ticker", "")).strip().upper()
           for s in syms
           if s.get("effective", True) and s.get("ticker")]
    if not out:
        raise SystemExit(
            f"ERROR: universe {name!r} resolved to 0 effective symbols — "
            f"refusing to run on an empty universe."
        )
    return out


def _resolve_sleeves(
    spec: str, capital_usd: float
) -> tuple[list[str], dict[str, float], dict[str, dict[str, Any]]]:
    """Parse '<name>:<size>,...' into (union, {symbol: capital_per_slot}, sleeve_map).

    Capital splits EQUALLY across sleeves; within a sleeve each name gets
    sleeve_capital/sleeve_size (the trader's 20/30/1). A sleeve name resolves
    to a universe's symbols, or — if there's no such universe (404) — to a
    single bare ticker (e.g. GLD). On overlap the FIRST sleeve wins (a name
    can hold only one live position). Fail-loud on a non-404 universe error
    (a real universe that's temporarily unreadable must not silently become a
    bogus ticker).

    `sleeve_map` is {name: {"symbols": [...], "size": N}} — the membership +
    slot count the strategy needs for top-N-by-conviction selection. A symbol
    appears in exactly ONE sleeve (the first that claimed it), so the slot
    accounting matches the capital map."""
    import requests
    sleeves: list[tuple[str, int]] = []
    for part in spec.split(","):
        name, _, size = part.partition(":")
        name = name.strip()
        if name:
            sleeves.append((name, int(size) if size.strip() else 1))
    if not sleeves:
        raise SystemExit(f"ERROR: --sleeves {spec!r} parsed to no sleeves")
    sleeve_capital = capital_usd / len(sleeves)
    union: list[str] = []
    per_cap: dict[str, float] = {}
    sleeve_map: dict[str, dict[str, Any]] = {}
    for name, size in sleeves:
        try:
            syms = _fetch_universe_symbols(name)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                syms = [name.upper()]   # not a universe → a single ticker (GLD)
            else:
                raise
        per_slot = sleeve_capital / max(1, size)
        owned: list[str] = []           # names this sleeve actually claims
        for s in syms:
            if s not in per_cap:        # first sleeve wins on overlap
                per_cap[s] = per_slot
                owned.append(s)
            if s not in union:
                union.append(s)
        sleeve_map[name] = {"symbols": owned, "size": size}
    return union, per_cap, sleeve_map


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    """Merge --symbol, --symbols and --universe into a deduplicated list.
    --sleeves (if given) wins: it sets the multi-sleeve universe + stashes the
    per-symbol capital map on args for the strategy. --universe accepts a
    comma-separated list of universes (deduped across overlaps)."""
    if getattr(args, "sleeves", None):
        syms, per_cap, sleeve_map = _resolve_sleeves(args.sleeves, args.capital_usd)
        args.per_symbol_capital = per_cap   # consumed by _build_strategy
        args.sleeves_map = sleeve_map       # drives top-N-by-conviction selection
        return syms
    args.per_symbol_capital = None
    args.sleeves_map = None
    out: list[str] = []
    if getattr(args, "universe", None):
        for uname in (u.strip() for u in args.universe.split(",") if u.strip()):
            for s in _fetch_universe_symbols(uname):
                if s not in out:
                    out.append(s)
    if args.symbols:
        for s in (x.strip().upper() for x in args.symbols.split(",") if x.strip()):
            if s not in out:
                out.append(s)
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


def _fetch_t212_tradeable_symbols() -> set[str] | None:
    """Bare strategy symbols T212 lists as tradeable, from the BACKEND's cached
    instruments registry (``GET /api/instruments/t212/tradeable``).

    The Mac paper-session daemon has NO direct T212 creds — orders execute
    server-side via the .NET OMS ApproveAsync, which holds the creds — so the
    broker catalog is read from the backend (which DOES have them). Returns
    ``None`` on any failure (or when T212 isn't enabled server-side) so the
    caller FAILS OPEN and trades the full universe rather than halting."""
    try:
        import requests
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{base.rstrip('/')}/api/instruments/t212/tradeable"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("enabled"):
            return None
        syms = body.get("symbols") or []
        return {str(s).upper() for s in syms} or None
    except Exception:  # noqa: BLE001 — fail open, never block trading
        logging.getLogger("tradepro.cli").warning(
            "T212 tradeable-set fetch from backend failed — universe "
            "validation FAILS OPEN this run", exc_info=True,
        )
        return None


def _validate_universe_against_broker(
    args: argparse.Namespace, symbols: list[str]
) -> list[str]:
    """Filter the resolved universe to symbols the TARGET BROKER can actually
    trade — PER BROKER, because T212 ≠ IG ≠ IBKR each list different
    instruments. The Wikipedia→DB universe says what we'd LIKE to trade; this
    intersects it with what the broker will ACCEPT, so names like WFRD/LFUS/JEF
    (valid high-beta tickers, just not on T212) get dropped here instead of
    404-ing at the order router every 5-minute cycle.

    FAIL OPEN: if the broker catalog can't be fetched we trade the full
    universe (a fetch hiccup must not halt the desk). Prunes
    ``args.per_symbol_capital`` + ``args.sleeves_map`` in lockstep so the
    sizing maps never reference a dropped name. Stashes the dropped list on
    ``args.dropped_untradeable`` for snapshot surfacing."""
    log = logging.getLogger("tradepro.cli")
    args.dropped_untradeable = []
    broker = (getattr(args, "broker", "") or "").lower()
    if not symbols or "t212" not in broker:
        # Only T212 equity validation is wired today; IG/IBKR pass through
        # (their FX/CFD epics are already validated via the static epic maps).
        return symbols

    from ..paper.brokers.t212 import _T212_FX_TICKER

    catalog = _fetch_t212_tradeable_symbols()
    if not catalog:
        log.warning(
            "universe validation skipped: T212 catalog unavailable "
            "(fail-open — trading full %d-symbol universe)", len(symbols),
        )
        return symbols

    kept: list[str] = []
    dropped: list[str] = []
    for s in symbols:
        # Tradeable if T212 lists it, or it's a mapped FX pair, or it's already
        # a fully-qualified broker ticker (has a suffix).
        if s in catalog or s.upper() in _T212_FX_TICKER or "_" in s:
            kept.append(s)
        else:
            dropped.append(s)

    if dropped:
        log.warning(
            "universe validation: %d/%d symbol(s) NOT tradeable on T212 — "
            "dropped before sizing: %s", len(dropped), len(symbols), dropped,
        )
        if getattr(args, "per_symbol_capital", None):
            for s in dropped:
                args.per_symbol_capital.pop(s, None)
        if getattr(args, "sleeves_map", None):
            for sl in args.sleeves_map.values():
                sl["symbols"] = [x for x in sl.get("symbols", []) if x not in dropped]
        args.dropped_untradeable = dropped

    return kept


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


def _pct_to_fraction(v):
    """Normalise a risk-control percent to the fraction the strategy compares
    against. CLI/config convention is WHOLE PERCENT (--stop-loss-pct 8 = 8%),
    but ichimoku_equity's risk-exit compares against a fractional unrealised
    return ((mark-entry)/entry), so 8 must become 0.08. A value > 1 is a
    percent (÷100); a value already in (0, 1] is treated as a fraction; None
    stays None (control off). Fixes stops never firing because 8 was read as
    a −800% threshold."""
    if v is None:
        return None
    try:
        f = abs(float(v))
    except (TypeError, ValueError):
        return None
    return f / 100.0 if f > 1.0 else f


def _parse_excluded(val) -> frozenset:
    """Normalise --exclude-symbols / runtime_config into a frozenset of
    upper-cased symbols. Accepts a comma-string ("TSLA,GME") or a JSON list
    (["TSLA","GME"]) from config; None/empty → no exclusions."""
    if not val:
        return frozenset()
    items = val if isinstance(val, (list, tuple, set)) else str(val).split(",")
    return frozenset(s.strip().upper() for s in items if s and str(s).strip())


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
                max_daily_loss_usd=args.max_daily_loss_usd,
                max_drawdown_pct=_pct_to_fraction(args.max_drawdown_pct),
                max_open_positions=args.max_open_positions,
                max_position_pct_of_capital=_pct_to_fraction(args.max_position_pct_of_capital),
                excluded_symbols=_parse_excluded(args.exclude_symbols),
            ),
        )

    if strategy_name == "ichimoku_equity":
        from ..paper.strategies.ichimoku_equity import IchimokuEquityStrategy
        return IchimokuEquityStrategy(
            strategy_id=strategy_id,
            params={
                "symbols": symbols,
                "broker": args.broker,   # drives broker-capability gating (MOO vs gate-at-open)
                "capital_usd": args.capital_usd,
                "sleeve_size": args.sleeve_size,
                # Per-sleeve capital map from --sleeves (None → flat sizing).
                "per_symbol_capital": getattr(args, "per_symbol_capital", None),
                # Per-sleeve membership + slot counts → top-N-by-conviction
                # selection so held names never exceed capital (None → no cap).
                "sleeves": getattr(args, "sleeves_map", None),
                "target_vol": args.target_vol,
                "max_leverage": args.max_leverage,
                "use_regime_filter": not args.no_regime_filter,
                # Risk controls — None by default (T212 control unchanged);
                # set via --stop-loss-pct/--take-profit-pct/--max-per-sector
                # for the protected IBKR-paper clone.
                # Percent → fraction: CLI/config pass whole percents (8 = 8%)
                # but the strategy compares against a fractional return.
                "stop_loss_pct": _pct_to_fraction(getattr(args, "stop_loss_pct", None)),
                "take_profit_pct": _pct_to_fraction(getattr(args, "take_profit_pct", None)),
                "max_per_sector": getattr(args, "max_per_sector", None),
                # Entry-extension "don't chase" gate (whole percent / RSI level).
                "entry_max_ext_pct": getattr(args, "entry_max_ext_pct", None),
                "entry_rsi_max": getattr(args, "entry_rsi_max", None),
                # Primary-trend floor (clone deviation): block new longs below
                # their own 200-SMA. OFF for the verbatim T212 control.
                "entry_require_above_200sma": bool(getattr(args, "entry_require_above_200sma", False)),
                "entry_veto_ma_suspect": bool(getattr(args, "entry_veto_ma_suspect", False)),
                # Entry-quality gate: skip a NEW long that's an RS laggard or on
                # thin volume (the ANET case). OFF for the verbatim T212 control;
                # enabled on the protected clone via runtime_config.
                "entry_quality_gate": bool(getattr(args, "entry_quality_gate", False)),
                "entry_min_rs": getattr(args, "entry_min_rs", 5.0),
                "entry_min_volume_ratio": getattr(args, "entry_min_volume_ratio", 0.8),
                # Earnings-proximity gate: skip a NEW long into an earnings blackout.
                "entry_earnings_gate": bool(getattr(args, "entry_earnings_gate", False)),
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
                max_daily_loss_usd=args.max_daily_loss_usd,
                max_drawdown_pct=_pct_to_fraction(args.max_drawdown_pct),
                max_open_positions=args.max_open_positions,
                max_position_pct_of_capital=_pct_to_fraction(args.max_position_pct_of_capital),
                excluded_symbols=_parse_excluded(args.exclude_symbols),
            ),
        )

    if strategy_name == "intraday_flat":
        from ..paper.strategies.intraday_flat import IntradayFlatStrategy
        ix_params = {
            # The scanner ranks `candidates` then intersects with the IG
            # epic map; pass the daemon's --symbols as the candidate set.
            "candidates": symbols,
            "capital_usd": args.capital_usd,
            "risk_per_trade_usd": args.risk_per_trade_usd,
            # Route to IG (matches --broker ig). Orders carry this
            # broker_label + the per-symbol epic from ig_epic_map.json.
            "broker_label": "IG_DEMO",
        }
        # Selectivity / cost-aware overrides — only when set, so an unset
        # value never clobbers default_params (e.g. top_n=None would break).
        for _k in ("top_n", "min_atr_pct", "min_strength"):
            _v = getattr(args, _k, None)
            if _v is not None:
                ix_params[_k] = _v
        return IntradayFlatStrategy(
            strategy_id=strategy_id,
            params=ix_params,
            # Long-only intraday by design — never short.
            risk=RiskLimits(
                max_position_value_usd=args.max_position_value_usd,
                allow_short=False,
                max_daily_loss_usd=args.max_daily_loss_usd,
                max_drawdown_pct=_pct_to_fraction(args.max_drawdown_pct),
                max_open_positions=args.max_open_positions,
                max_position_pct_of_capital=_pct_to_fraction(args.max_position_pct_of_capital),
                excluded_symbols=_parse_excluded(args.exclude_symbols),
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
    # IBKR paper account (DUP656969). WARNING: this account is SHARED between the
    # equity clone (ichimoku_equity_ibkr) AND the FX clone (ichimoku_fx_mr_ibkr),
    # so its /positions mixes equities AND FX. The orphaned-exit union MUST filter
    # to the running strategy's asset class (see _held_for_strategy_asset_class) or
    # a strategy will pull the OTHER's instruments into its book (the FX-sold-META
    # bug: the FX sweep saw the equity clone's META and emitted SELL META).
    "ibkr": "/api/integrations/ibkr/positions",
}

# ISO currency codes the FX clone trades. A bare held ticker is an FX PAIR when it
# is two of these concatenated (EURUSD, GBPJPY) — used to keep the shared IBKR
# account's equities OUT of the FX book and its FX pairs OUT of the equity book.
_FX_CCY = frozenset({"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK"})


def _is_fx_pair(sym: str) -> bool:
    s = (sym or "").replace("=X", "").upper().strip()
    return len(s) == 6 and s[:3] in _FX_CCY and s[3:] in _FX_CCY


def _held_for_strategy_asset_class(held, strategy_name: str):
    """Filter broker-held names to the running strategy's asset class, so the
    orphaned-exit union on a SHARED account (IBKR paper) never pulls the other
    strategy's instruments. FX strategies keep only FX pairs; everything else
    (equity) keeps only non-FX. A no-op for single-asset brokers (T212 = all
    equities anyway). Accepts either a list of symbols OR a {symbol: mark} dict
    (the two shapes the held-position helpers return) and preserves that shape."""
    is_fx = "fx" in (strategy_name or "").lower()
    if isinstance(held, dict):
        return {s: p for s, p in held.items() if _is_fx_pair(s) == is_fx}
    return [s for s in (held or []) if _is_fx_pair(s) == is_fx]


def broker_requires_position_seed(broker: str) -> bool:
    """True when `broker` holds positions across runs and must therefore
    be seeded from before trading (fail-closed by default for any broker
    not explicitly known to be a sim)."""
    return broker.strip().lower() not in _SIM_BROKERS


def _parse_broker_position_rows(
    rows: list[dict], universe: set[str],
) -> tuple[dict[str, int], dict[str, float]]:
    """Pure: broker position rows → ({bare_symbol: signed_int_qty},
    {bare_symbol: avg_entry_price}), filtered to ``universe`` (empty universe
    = no filter). Extracted from the broker seed so the epic-stripping +
    mini-lot handling is unit-testable without HTTP.

    The avg-price map is the broker's cost basis (averagePricePaid). The
    ledger needs it so unrealised P&L = (mark − avg) × qty is REAL — without
    it avg defaults to 0 and unrealised collapses to mark × qty (≈ position
    VALUE, not P&L), which made the cockpit P&L curve read ~$32k instead of
    the true +$284. Symbols with no/zero broker avg are omitted (the ledger
    leaves their cost basis untouched).

    The IG account is SHARED across strategies (FX pairs CS.D.*.MINI.IP,
    intraday_flat equity CFDs UA.D.*.CASH.IP, plus manual options DO.D./
    OD.D.*). Seeding a strategy with positions that aren't its own corrupts
    its delta math, so we strip broker suffixes to the bare symbol and keep
    only this strategy's universe.

    IG FX MINI positions report in MINI-LOTS (|qty| typically < 1.0, e.g.
    -0.7) and IG returns ONE ROW PER OPEN DEAL — a pair can have several. We
    therefore NET all rows for a pair FIRST (sum the signed mini-lots), then
    keep the SIGN (+/-1 unit). This fixes two bugs in the old "collapse each
    deal to +/-1, then sum" approach:
      • OVERCOUNT — 3 short deals (-1.1,-0.7,-0.4) summed to -3 instead of the
        intended one signed unit, so the strategy mis-read `current` and
        churned / stacked deals.
      • WRONG CANCEL — opposing deals (-0.7 and +0.4) cancelled to 0 ("flat")
        even though the true net is short.
    Exact multi-unit magnitude still needs the per-pair IG contract size to
    convert mini-lots → units (backend follow-up); the SIGN is enough for the
    delta math to read delta=0 on the dominant +/-1 target."""
    # ── Pass 1: net the raw quantity per bare symbol (across all deals) ──
    net_raw: dict[str, float] = {}
    is_mini: dict[str, bool] = {}
    avg_num: dict[str, float] = {}  # Σ(avg × |raw|) — |raw|-weighted cost basis
    avg_den: dict[str, float] = {}  # Σ|raw| over rows that reported an avg > 0
    for r in rows:
        t = (r.get("ticker") or r.get("epic") or "").upper()
        if not t:
            continue
        # Strip broker suffixes. IG epics are <class>.D.<sym>.<*>.IP:
        #   CS.D.EURUSD.MINI.IP  -> EURUSD   (FX)
        #   UA.D.AAPL.CASH.IP    -> AAPL     (equity CFD)
        #   AAPL_US_EQ           -> AAPL     (T212)
        # Options (DO.D.EURO.42.IP / OD.D.WK2EURO.32.IP) strip to a token
        # that matches no strategy universe -> filtered out below.
        bare = t
        if t.endswith(".IP") and "." in t:
            parts = t.split(".")
            if len(parts) >= 4:
                bare = parts[2]
        elif "_" in t:
            bare = t.split("_", 1)[0]
        # Corporate-action rename: a broker may still report a position under
        # the OLD ticker (T212 holds Bath & Body Works as LB_US_EQ) after the
        # universe/signal/data all moved to the CURRENT ticker (BBWI).
        # Canonicalise BEFORE the universe filter so the held name is kept,
        # priced, and exited under ONE identity — otherwise the old ticker
        # falls outside the universe, drops from the seed, and the strategy
        # re-buys every cycle (guard blind) while the orphan can't be exited.
        bare = canonical_ticker(bare)
        if universe and bare not in universe:
            continue
        try:
            raw = float(r.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if raw == 0:
            continue
        net_raw[bare] = net_raw.get(bare, 0.0) + raw
        # A pair is "mini" if ANY of its rows is a MINI epic (rows are
        # homogeneous per pair in practice).
        is_mini[bare] = is_mini.get(bare, False) or (".MINI." in t)
        # Broker cost basis for honest unrealised P&L. T212 + IG both report
        # it as averagePricePaid (IG can be null → skip). |raw|-weight so a
        # netted multi-deal position carries a sensible blended entry.
        avg_raw = (r.get("averagePricePaid")
                   if r.get("averagePricePaid") is not None
                   else r.get("avgPrice"))
        try:
            avg = float(avg_raw) if avg_raw is not None else 0.0
        except (TypeError, ValueError):
            avg = 0.0
        if avg > 0:
            w = abs(raw)
            avg_num[bare] = avg_num.get(bare, 0.0) + avg * w
            avg_den[bare] = avg_den.get(bare, 0.0) + w

    # ── Pass 2: convert each pair's NET to a seedable signed quantity ──
    positions: dict[str, int] = {}
    avg_prices: dict[str, float] = {}
    for bare, net in net_raw.items():
        if is_mini.get(bare):
            # IG FX MINI: net the mini-lots, then keep the SIGN (+/-1 unit).
            qty = 0 if abs(net) < 1e-9 else (1 if net > 0 else -1)
        else:
            # Whole-unit equities/CFDs: truncate the NET toward zero so we
            # never overstate the held quantity (T212 fractional shares /
            # IG equity CFDs are whole units; rounding up would trigger
            # "selling more than owned").
            qty = int(net)
        if qty != 0:
            positions[bare] = qty
            if avg_den.get(bare, 0.0) > 0:
                avg_prices[bare] = avg_num[bare] / avg_den[bare]
    return positions, avg_prices


def _fetch_broker_held_symbols(broker: str) -> list[str]:
    """Bare symbols the strategy currently HOLDS at `broker` (golden source).

    Used to union held names into the universe so a name that has rotated OUT
    of the sleeve universe but is STILL HELD keeps getting bars + an exit
    evaluation (the orphaned-hold fix: without this, a held name outside the
    universe gets no bar → on_bar never runs its exit → it's stuck forever).
    Best-effort — returns [] on any error (the union is an enhancement, not a
    correctness gate; the seed itself still confirms the book separately)."""
    b = broker.strip().lower()
    path = _REAL_BROKER_POSITION_PATHS.get(b)
    if path is None:
        return []
    try:
        import requests
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(f"{base.rstrip('/')}{path}", headers=headers, timeout=10)
        resp.raise_for_status()
        rows = resp.json().get("positions") or []
        positions, _ = _parse_broker_position_rows(rows, None)  # None = no universe filter
        return [s for s, q in positions.items() if q != 0]
    except Exception:  # noqa: BLE001
        logging.getLogger("tradepro.cli").warning(
            "orphaned-exit: could not read %s held positions for the universe union", b)
        return []


def _fetch_broker_held_marks(broker: str) -> dict[str, float]:
    """{bare_symbol: current mark} for held positions at `broker` (golden source).

    Prices the held-exit RECONCILIATION bars (see HeldReconciliationBus): a held
    name whose live feed delivered no trigger bar still needs an on_bar → exit
    evaluation, and the synthetic bar's price feeds the (clone's) stop-loss check,
    so we use the broker's real current mark rather than a placeholder. Best-effort
    — returns {} on any error (reconciliation is exit-coverage, not a correctness
    gate; the seed still confirms the book separately)."""
    b = broker.strip().lower()
    path = _REAL_BROKER_POSITION_PATHS.get(b)
    if path is None:
        return {}
    try:
        import requests
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(f"{base.rstrip('/')}{path}", headers=headers, timeout=10)
        resp.raise_for_status()
        out: dict[str, float] = {}
        for r in resp.json().get("positions") or []:
            sym = (r.get("yahooSymbol") or r.get("ticker") or r.get("epic") or "")
            sym = sym.replace("_US_EQ", "").upper().strip()
            px = r.get("currentPrice") or r.get("mark") or r.get("price")
            qty = r.get("quantity") or r.get("qty") or 0
            if sym and px and qty:
                try:
                    out[sym] = float(px)
                except (TypeError, ValueError):
                    continue
        return out
    except Exception:  # noqa: BLE001
        logging.getLogger("tradepro.cli").warning(
            "held-exit reconciliation: could not read %s held marks", b)
        return {}


def _overlay_live_marks_and_pnl(snapshot: dict, broker: str, log) -> None:
    """Re-mark every open position in the snapshot to the broker's LIVE current
    price (golden source) and recompute SINCE-ENTRY P&L off it — so the displayed
    unrealised P&L can never drift from broker truth.

    The bug this fixes (ANET): the ledger's ``last_mark`` is the last BAR it saw.
    For a held/thin name with no intraday trigger that's the reconciliation bar's
    DAILY CLOSE — priced there deliberately so the EXIT signal evaluates
    deterministically (see the HeldReconciliationBus comment). That stale close
    then leaked into the displayed P&L: ANET read +$4.92 when the broker's live
    mark said -$13.32. Here we correct only the DISPLAY mark; the reconciliation
    bar that drives on_bar / exit evaluation is left untouched, so strategy
    behaviour + parity are unchanged (feedback_strategy_verbatim_port_parity).

    Fail-loud, never fabricate (feedback_no_false_positives):
      - ``mark_source``  = "broker_live" | "ledger_stale" | "no_mark"
      - ``mark_is_stale`` flags any non-live mark so the UI can grey the number
      - ``since_entry_pnl`` = (live_mark - avg_entry) * qty; set to None (NOT 0,
        not a guess) when we lack a trustworthy cost basis (avg_entry<=0) or a
        usable mark — the number is unknown, and we say so rather than invent one.
    """
    live = _fetch_broker_held_marks(broker)   # {SYM: currentPrice}; {} on error
    for book in snapshot.get("strategies") or []:
        positions = book.get("positions") or []
        u_total = 0.0
        n_unknown = 0
        for pos in positions:
            sym = str(pos.get("symbol") or "").upper().strip()
            qty = pos.get("quantity") or 0
            avg = pos.get("avg_entry_price") or 0.0
            lm = live.get(sym)
            if lm and lm > 0:
                pos["last_mark"] = round(float(lm), 4)
                pos["mark_source"] = "broker_live"
                pos["mark_is_stale"] = False
            else:
                # Keep whatever mark the ledger had, but say plainly it's not live.
                pos["mark_source"] = "ledger_stale" if pos.get("last_mark") else "no_mark"
                pos["mark_is_stale"] = True
            mark = pos.get("last_mark") or 0.0
            if avg > 0 and mark > 0:
                sep = round((mark - avg) * qty, 2)
                pos["since_entry_pnl"] = sep
                pos["unrealised_pnl"] = sep      # since-entry IS the open MTM P&L
                u_total += sep
            else:
                pos["since_entry_pnl"] = None     # no trustworthy cost basis → unknown
                pos["mark_is_stale"] = True
                n_unknown += 1
        # Roll the corrected per-position P&L up to the strategy level.
        realised = book.get("realised_pnl") or 0.0
        book["unrealised_pnl"] = round(u_total, 2)
        book["equity"] = round(realised + u_total, 2)
        book["marks_source"] = "broker_live" if live else "ledger_only"
        if n_unknown:
            book["unrealised_pnl_partial"] = True
            book["positions_without_cost_basis"] = n_unknown
    if not live:
        log.warning(
            "live-mark overlay: broker %s returned no live marks — displayed P&L "
            "falls back to ledger (possibly stale) marks; positions flagged stale",
            broker)


def _latest_daily_closes(symbols: list[str], cache_dir: str) -> dict[str, float]:
    """{bare_symbol: latest daily close} from the local bar cache — used to PRICE
    the signal-reconciliation bars for UNIVERSE names (entry coverage). The close
    feeds the entry sizing (qty = notional // close), so it must be real. Best-effort
    per symbol; a symbol with no cache is simply not reconciled (stays feed-dependent
    + will surface as a no-bar flag). Kept lightweight (index-free parquet read)."""
    import glob as _glob
    out: dict[str, float] = {}
    try:
        import pandas as _pd
    except Exception:  # noqa: BLE001
        return out
    for s in symbols:
        try:
            files = sorted(_glob.glob(f"{cache_dir}/{s}/1d/*.parquet"))
            if not files:
                continue
            df = _pd.read_parquet(files[-1])
            df.columns = [c.lower() for c in df.columns]
            if "close" in df.columns and len(df):
                px = float(df["close"].iloc[-1])
                if px > 0:
                    out[s] = px
        except Exception:  # noqa: BLE001 — one bad symbol never blocks the rest
            continue
    return out


def _fetch_ibkr_rows_via_webapi() -> list[dict] | None:
    """Effective IBKR paper book (held positions + pending orders) via the Web
    API — the RELIABLE path with NO desktop-Gateway (:7500) dependency, the same
    model as T212's REST-API seed. Returns rows [{ticker, quantity,
    averagePricePaid}] (matching the gateway read), or None when the API is
    unreachable / IBKR disabled server-side so the caller falls through to the
    gateway path. Raises on a server-side error so the caller logs + degrades.
    """
    import requests
    from . import push_to_api
    base, token = push_to_api.load_credentials()
    base = (base or "").rstrip("/")
    if not base:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    rp = requests.get(f"{base}/api/integrations/ibkr/positions", headers=headers, timeout=15)
    rp.raise_for_status()
    pj = rp.json()
    if not pj.get("enabled", True):
        return None
    if pj.get("error"):
        raise RuntimeError(f"ibkr web positions error: {pj['error']}")

    eff: dict[str, float] = {}
    avg: dict[str, float] = {}
    for p in (pj.get("positions") or []):
        sym = p.get("ticker")
        if not sym:
            continue
        try:
            eff[sym] = eff.get(sym, 0.0) + float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        c = p.get("averagePricePaid")
        if c:
            try:
                avg[sym] = float(c)
            except (TypeError, ValueError):
                pass

    # Pending live orders count toward the effective book (a pre-market MOO sits
    # PreSubmitted; counting it stops the desk re-placing + stacking duplicates).
    ro = requests.get(f"{base}/api/integrations/ibkr/orders", headers=headers, timeout=15)
    ro.raise_for_status()
    oj = ro.json()
    if oj.get("error"):
        raise RuntimeError(f"ibkr web orders error: {oj['error']}")
    for o in (oj.get("orders") or []):
        st = (o.get("status") or "").lower()
        if any(t in st for t in ("fill", "cancel", "inactive")):
            continue
        sym = o.get("symbol")
        if not sym:
            continue
        try:
            rem = float(o.get("remainingQty") or o.get("totalSize") or 0)
        except (TypeError, ValueError):
            rem = 0.0
        side = (o.get("side") or "").upper()
        eff[sym] = eff.get(sym, 0.0) + rem * (1 if side == "BUY" else -1)

    return [
        {"ticker": s, "quantity": q, "averagePricePaid": avg.get(s, 0.0)}
        for s, q in eff.items() if q != 0
    ]


def _seed_strategy_positions_from_broker(strategy, broker: str) -> tuple[dict[str, int], dict[str, float]]:
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

    Returns ({symbol: signed_int_qty}, {symbol: avg_entry_price}). An EMPTY
    positions dict is a positive result: the broker confirmed a genuinely
    flat book, so opening new positions is safe. (A failure to reach the
    broker is NOT an empty dict — it raises.) The avg-price map is the
    broker cost basis for the ledger's unrealised-P&L math.
    """
    b = broker.strip().lower()
    if not hasattr(strategy, "seed_positions"):
        raise PositionSeedError(
            f"strategy {getattr(strategy, 'strategy_id', '?')!r} has no "
            f"seed_positions hook — cannot confirm position from {b!r}"
        )
    # IBKR: read OUR PAPER account straight from the Gateway via ib_insync
    # (TRADEPRO_IBKR_PORT/ACCOUNT). NOT /api/integrations/ibkr — that endpoint
    # reads the LIVE harvesting account (IBKR_LIVE), the WRONG book. Self-
    # contained (returns early) so the t212/ig HTTP seed path is untouched.
    # Fail-closed: any connection error raises (never trade on an unconfirmed book).
    if b == "ibkr":
        log = logging.getLogger("tradepro.cli")
        import asyncio as _aio
        import os as _os
        from ib_insync import IB as _IB
        _host = _os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
        _port = int(_os.environ.get("TRADEPRO_IBKR_PORT", "7497"))
        _want = (_os.environ.get("TRADEPRO_IBKR_ACCOUNT") or "").strip()
        # Distinct clientId from the engine's so this short-lived seed
        # connection never clashes with the router/bus session.
        _cid = int(_os.environ.get("TRADEPRO_IBKR_CLIENT_ID", "17")) + 100

        async def _fetch_ibkr_rows() -> list[dict]:
            ib = _IB()
            await ib.connectAsync(_host, _port, clientId=_cid, timeout=15)
            try:
                await _aio.sleep(1.0)  # let position snapshots arrive
                # EFFECTIVE book = filled positions + PENDING orders (OPG/working).
                # MOO orders queue for the auction, so between the pre-market run
                # and the open the positions read FLAT while OPG orders sit
                # PreSubmitted — seeding flat would re-place every 15-min run and
                # STACK duplicates. Counting pending orders as committed makes the
                # strategy see target==current → no re-emit. Golden source = the
                # broker's own positions + open orders.
                eff: dict[str, float] = {}
                avg: dict[str, float] = {}
                for pp in ib.positions():
                    if _want and (pp.account or "").strip() != _want:
                        continue
                    s = pp.contract.symbol
                    eff[s] = eff.get(s, 0.0) + pp.position
                    if pp.avgCost:
                        avg[s] = pp.avgCost
                try:
                    await _aio.wait_for(ib.reqAllOpenOrdersAsync(), 6)
                except Exception:
                    pass
                await _aio.sleep(0.5)
                for t in ib.openTrades():
                    if t.orderStatus.status in (
                        "Cancelled", "Filled", "Inactive", "ApiCancelled"):
                        continue
                    if _want and (t.order.account or "").strip() != _want:
                        continue
                    s = t.contract.symbol
                    eff[s] = eff.get(s, 0.0) + t.order.totalQuantity * (
                        1 if t.order.action == "BUY" else -1)
                return [
                    {"ticker": s, "quantity": q, "averagePricePaid": avg.get(s, 0.0)}
                    for s, q in eff.items() if q != 0
                ]
            finally:
                ib.disconnect()

        # Read fresh with RETRY + a resilience CACHE. Under harvest/trade
        # contention on the one IBKR account, this positions read transiently
        # times out — and a single failure used to fail-close the whole desk
        # (the ×32/×36 "could not confirm position" aborts). Retry a few times
        # (most blips pass on the 2nd try), cache every good snapshot, and only
        # if ALL retries fail fall back to the last-good cached snapshot WHEN IT
        # IS FRESH ENOUGH — otherwise still fail-closed (never trade on a stale
        # or unknown book; broker stays the golden source). TTL is conservative +
        # tunable via TRADEPRO_IBKR_POS_CACHE_TTL_S.
        import json as _json
        import time as _time
        from pathlib import Path as _Path
        _cache_dir = _Path.home() / ".tradepro" / "cache" / "ibkr-positions"
        _sid = str(getattr(strategy, "strategy_id", "unknown"))
        _cache_file = _cache_dir / f"{_sid}.json"
        _stale_ttl = float(_os.environ.get("TRADEPRO_IBKR_POS_CACHE_TTL_S", "600"))

        rows = None
        _last_exc: Exception | None = None
        # PHASE 2 — prefer the CENTRAL GATEWAY's fresh shared snapshot. The
        # ibkr_gateway daemon holds the ONE connection and refreshes _gateway.json
        # every ~30s; reading it means this desk does NOT open its own connection,
        # which is what removes the per-account contention (and the ×32/×36 aborts)
        # entirely. Degrades safely: if the gateway isn't running / its cache is
        # stale, fall through to the direct read below.
        _gw_file = _cache_dir / "_gateway.json"
        _gw_ttl = float(_os.environ.get("TRADEPRO_IBKR_GATEWAY_TTL_S", "90"))
        try:
            _g = _json.loads(_gw_file.read_text())
            _gage = _time.time() - float(_g.get("ts", 0))
            if _gage <= _gw_ttl:
                rows = _g.get("rows")
                log.info("POSITION SEED (ibkr-gateway): %s via central gateway snapshot "
                         "%.0fs old (%d positions) — no per-desk connection",
                         _sid, _gage, len(rows or []))
        except Exception:  # noqa: BLE001 — gateway down / stale → direct read
            rows = None
        # Direct read (with retry) ONLY if the gateway didn't give a fresh snapshot.
        # PRIMARY = the IBKR Web API (reliable, NO desktop-Gateway :7500 dependency
        # — the clone fail-closed all day when :7500 was refused). The legacy
        # Gateway read is now only a last-resort fallback if the Web API is
        # disabled server-side. "We've moved to API."
        if rows is None:
            for _attempt in range(3):
                try:
                    rows = _fetch_ibkr_rows_via_webapi()
                    if rows is not None:
                        log.info("POSITION SEED (ibkr-webapi): %s — %d position(s) via "
                                 "the IBKR Web API (no Gateway dependency)", _sid, len(rows))
                        break
                    # None ⇒ IBKR disabled server-side → last-resort legacy Gateway.
                    rows = _aio.run(_fetch_ibkr_rows())
                    break
                except Exception as exc:  # noqa: BLE001
                    _last_exc = exc
                    log.warning("ibkr positions read attempt %d/3 (webapi→gateway) failed: %s",
                                _attempt + 1, exc)
                    if _attempt < 2:
                        _time.sleep(2.0 * (_attempt + 1))

        if rows is not None:
            try:  # cache the confirmed snapshot for the fallback path
                _cache_dir.mkdir(parents=True, exist_ok=True)
                _cache_file.write_text(_json.dumps({"ts": _time.time(), "rows": rows}))
            except Exception:  # noqa: BLE001 — caching is best-effort
                pass
        else:
            cached_rows = None
            try:
                _obj = _json.loads(_cache_file.read_text())
                _age = _time.time() - float(_obj.get("ts", 0))
                if _age <= _stale_ttl:
                    cached_rows = _obj.get("rows")
                    log.warning(
                        "ibkr positions read failed after 3 retries (%s) — using "
                        "cached snapshot %.0fs old (TTL %.0fs) so the desk keeps "
                        "running instead of fail-closing", _last_exc, _age, _stale_ttl)
            except Exception:  # noqa: BLE001
                cached_rows = None
            if cached_rows is None:
                raise PositionSeedError(
                    f"could not read ibkr paper positions (golden source) via the "
                    f"Gateway on :{_port} after 3 retries, and no fresh cache "
                    f"(TTL {_stale_ttl:.0f}s): {_last_exc}"
                ) from _last_exc
            rows = cached_rows
        pp = getattr(strategy, "params", {}) or {}
        universe: set[str] = set()
        for key in ("pairs", "symbols", "candidates"):
            universe.update(str(x).strip().upper() for x in (pp.get(key) or []) if str(x).strip())
        positions, avg_prices = _parse_broker_position_rows(rows, universe)
        if positions:
            log.info("POSITION SEED (ibkr-broker): %s starting with %s",
                     strategy.strategy_id, positions)
            # Pass the broker cost basis so risk-control exits (stop-loss /
            # take-profit) evaluate against a real entry — without it the
            # seeded book has avg_entry_price=0 and stops never fire.
            strategy.seed_positions(positions, avg_prices)
        else:
            log.info("POSITION SEED (ibkr-broker): %s — paper account confirms a flat book",
                     strategy.strategy_id)
        return positions, avg_prices
    path = _REAL_BROKER_POSITION_PATHS.get(b)
    if path is None:
        raise PositionSeedError(
            f"no positions endpoint wired for broker {b!r} — refusing to "
            f"trade without confirming the current position from the "
            f"golden source. Add it to _REAL_BROKER_POSITION_PATHS."
        )
    log = logging.getLogger("tradepro.cli")
    import time as _time

    import requests
    from . import push_to_api
    base, token = push_to_api.load_credentials()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = base.rstrip('/')

    # Retry-with-backoff before fail-closed. The positions endpoint is down
    # while the API restarts on every deploy — and a .NET cold start (Postgres
    # migrations + secrets) takes 60-90s (see aws-deploy "start_period 90s"),
    # so the old 6s retry never had a chance and the abort fired a CRITICAL
    # alert on every deploy. Span a full cold start (~80s) so a deploy blip is
    # ridden out, but STILL fail closed on a genuine outage — we never trade
    # on an unconfirmed book. Backoffs sum ~80s across 6 attempts.
    backoffs = [5.0, 10.0, 15.0, 20.0, 30.0]
    last_exc: Exception | None = None
    rows = None
    for attempt in range(len(backoffs) + 1):
        try:
            resp = requests.get(f"{base}{path}", headers=headers, timeout=10)
            resp.raise_for_status()
            rows = resp.json().get("positions") or []
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < len(backoffs):
                log.warning(
                    "could not read %s positions (attempt %d/%d): %s — "
                    "retrying in %.0fs",
                    b, attempt + 1, len(backoffs) + 1, exc, backoffs[attempt],
                )
                _time.sleep(backoffs[attempt])
    if rows is None:
        # Exhausted retries — fail-closed: do NOT fall back to flat, do NOT
        # fall back to OMS. Raise so the caller aborts the session.
        raise PositionSeedError(
            f"could not read {b!r} positions (golden source) after "
            f"{len(backoffs) + 1} attempts: {last_exc}"
        ) from last_exc

    # Broker was readable → self-heal. Clear any prior fail-closed abort
    # alert for this (strategy, broker) so a transient blip that's now
    # recovered doesn't leave a sticky CRITICAL banner. Best-effort; matches
    # the dedup_key _abort_on_unconfirmed_position raises with.
    try:
        push_to_api.resolve_alert(
            base, token,
            dedup_key=f"position_seed_failed:{getattr(strategy, 'strategy_id', '?')}:{broker}")
    except Exception:  # noqa: BLE001
        pass

    # The IG account is SHARED across strategies (FX pairs CS.D.*.MINI.IP,
    # intraday_flat equity CFDs UA.D.*.CASH.IP, plus manual options DO.D./
    # OD.D.*). Seeding a strategy with positions that aren't its own corrupts
    # its delta math — e.g. ichimoku_fx_mr was seeded with the equity CFDs +
    # options, computed current==target on every pair, and went skip-no-delta
    # forever (so only 1 FX pair ever showed). Restrict the seed to THIS
    # strategy's declared universe.
    p = getattr(strategy, "params", {}) or {}
    universe: set[str] = set()
    for key in ("pairs", "symbols", "candidates"):
        universe.update(str(x).strip().upper() for x in (p.get(key) or []) if str(x).strip())

    positions, avg_prices = _parse_broker_position_rows(rows, universe)

    if positions:
        log.info(
            "POSITION SEED (%s-broker): %s starting with %s (cost basis for %d)",
            b, strategy.strategy_id, positions, len(avg_prices),
        )
        # Cost basis → stop-loss / take-profit can evaluate the seeded book.
        strategy.seed_positions(positions, avg_prices)
    else:
        log.info(
            "POSITION SEED (%s-broker): %s — broker confirms a flat book",
            b, strategy.strategy_id,
        )
    return positions, avg_prices


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


# Broker LABEL (strategy_broker_map) → --broker arg value.
_BROKER_LABEL_TO_ARG = {
    "T212_DEMO": "t212", "T212_LIVE": "t212",
    "IG_DEMO": "ig", "IG_LIVE": "ig",
    "IBKR_PAPER": "ibkr", "IBKR_LIVE": "ibkr",
    "PAPER": "yfinance",
}


def _apply_config_overrides(args, log) -> None:
    """--from-config: load this strategy's broker/account/runtime params from
    strategy_broker_map (the single source of truth) and apply them onto `args`,
    so the SAME command runs on Mac, AWS and prod. Requires --strategy-id."""
    import json as _json
    import os as _os
    import requests as _rq
    from . import push_to_api
    if not args.strategy_id:
        raise SystemExit("--from-config requires --strategy-id")
    base, token = push_to_api.load_credentials()
    if not base or not token:
        raise SystemExit("--from-config needs api-base-url + api-token (secrets)")
    h = {"Authorization": f"Bearer {token}"}
    resp = _rq.get(f"{base.rstrip('/')}/api/admin/strategy-broker-map",
                   headers=h, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    rows = body.get("mappings") or body.get("rows") or []
    row = next((m for m in rows if m.get("strategy_id") == args.strategy_id), None)
    if row is None:
        raise SystemExit(
            f"--from-config: no strategy_broker_map row for {args.strategy_id!r}")
    label = (row.get("broker") or "").upper()
    args.broker = _BROKER_LABEL_TO_ARG.get(label, label.lower())
    if row.get("account_id"):
        args.account = row["account_id"]
    cfg = row.get("runtime_config")
    if isinstance(cfg, str):
        cfg = _json.loads(cfg) if cfg.strip() else {}
    cfg = cfg or {}
    if cfg.get("strategy"):
        args.strategy = cfg["strategy"]
    # Apply param overrides ONLY when present in config (else keep CLI default).
    for key in ("sleeves", "symbols", "universe", "capital_usd", "lookback_days",
                "interval", "placement_mode", "stop_loss_pct", "take_profit_pct",
                "max_per_sector", "warmup_bars", "max_position_value_usd",
                "target_vol", "max_leverage", "sleeve_size",
                "entry_max_ext_pct", "entry_rsi_max", "entry_require_above_200sma", "entry_veto_ma_suspect",
                "entry_quality_gate", "entry_min_rs", "entry_min_volume_ratio",
                "entry_earnings_gate",
                "top_n", "min_atr_pct", "min_strength",
                "max_daily_loss_usd", "max_drawdown_pct",
                "max_open_positions", "max_position_pct_of_capital",
                "exclude_symbols", "reconcile_entries"):
        if key in cfg and cfg[key] is not None and hasattr(args, key):
            setattr(args, key, cfg[key])
    # IBKR connection → env (the adapter reads TRADEPRO_IBKR_*).
    if cfg.get("ibkr_port"):
        _os.environ["TRADEPRO_IBKR_PORT"] = str(cfg["ibkr_port"])
    if cfg.get("ibkr_client_id"):
        _os.environ["TRADEPRO_IBKR_CLIENT_ID"] = str(cfg["ibkr_client_id"])
    if row.get("account_id"):
        _os.environ["TRADEPRO_IBKR_ACCOUNT"] = row["account_id"]
    log.info(
        "--from-config applied for %s: broker=%s account=%s strategy=%s cfg=%s",
        args.strategy_id, args.broker, args.account, args.strategy,
        sorted(cfg.keys()))


def _fetch_ibkr_account_state_via_webapi(log) -> dict | None:
    """IBKR PAPER account-state (NLV / cash / unrealised P&L + position book) via
    the Web API ONLY — NO desktop Gateway (:7500). The Web-API replacement for the
    direct-Gateway account read: hits /api/integrations/ibkr/account-summary (the
    server combines ledger + positions) and returns the /api/ingest/account-state
    payload shape, or None when the API is unreachable / IBKR disabled server-side
    so the caller can fall through. daily_pnl (reqPnL) + session fills are
    Gateway-only, so they're absent here (daily_pnl carried through if present).
    Raises on a server-side error so the caller logs + degrades — never pushes a
    phantom zero."""
    import requests
    from . import push_to_api
    base, token = push_to_api.load_credentials()
    base = (base or "").rstrip("/")
    if not base:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(f"{base}/api/integrations/ibkr/account-summary", headers=headers, timeout=20)
    r.raise_for_status()
    j = r.json()
    if not j.get("enabled", True):
        return None
    if j.get("cashError") or j.get("positionsError"):
        raise RuntimeError(
            f"ibkr web account-summary error: cash={j.get('cashError')} pos={j.get('positionsError')}")
    positions = []
    for p in (j.get("positions") or []):
        positions.append({
            "symbol": p.get("symbol"),
            "secType": p.get("secType"),
            "right": None, "strike": None, "expiry": None,
            "qty": p.get("qty"),
            "mark": p.get("mark"),
            "marketValue": p.get("marketValue"),
            "avgCost": p.get("avgCost"),
            "unrealisedPnl": p.get("unrealisedPnl"),
            "currency": p.get("currency"),
        })
    return {
        "broker": "IBKR_PAPER",
        "account_id": None,
        "currency": j.get("currency"),
        "net_liquidation": j.get("netLiquidation"),
        "total_cash": j.get("cash"),
        "unrealised_pnl": j.get("unrealisedPnl"),
        "daily_pnl": j.get("dailyPnl"),
        "positions": positions,
    }


def _push_ibkr_account_state(account_id, base: str, token: str, log) -> None:
    """Push the IBKR PAPER account's NLV / cash / unrealised P&L + position book
    to /api/ingest/account-state so the cockpit can render the algo clone's OWN
    account row + per-position P&L. The live IBKRClient only sees the personal
    IBKR_LIVE account, so the clone (DUP656969) is otherwise invisible (£0/n.a).

    Best-effort: any error logs a warning and returns — it must never fail the
    trading session. Uses a distinct clientId so it can't clash with the
    router/bus or the position-seed connection sharing the same Gateway."""
    import asyncio as _aio
    import os as _os
    try:
        from ib_insync import IB as _IB
    except Exception as exc:  # noqa: BLE001
        log.warning("account-state: ib_insync unavailable (%s) — skip", exc)
        return
    _host = _os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    _port = int(_os.environ.get("TRADEPRO_IBKR_PORT", "7497"))
    _want = (str(account_id) if account_id else
             _os.environ.get("TRADEPRO_IBKR_ACCOUNT", "")).strip()
    _cid = int(_os.environ.get("TRADEPRO_IBKR_CLIENT_ID", "17")) + 200

    async def _read() -> dict:
        ib = _IB()
        await ib.connectAsync(_host, _port, clientId=_cid, timeout=15)
        try:
            await _aio.sleep(1.0)  # let portfolio + account snapshots arrive
            positions = []
            for it in ib.portfolio():
                if _want and (it.account or "").strip() != _want:
                    continue
                c = it.contract
                positions.append({
                    "symbol": c.symbol,
                    "secType": getattr(c, "secType", None) or None,
                    "right": getattr(c, "right", None) or None,
                    "strike": getattr(c, "strike", None) or None,
                    "expiry": getattr(c, "lastTradeDateOrContractMonth", None) or None,
                    "qty": it.position,
                    "mark": it.marketPrice,
                    "marketValue": it.marketValue,
                    "avgCost": it.averageCost,
                    "unrealisedPnl": it.unrealizedPNL,
                    "currency": getattr(c, "currency", None),
                })
            vals = ib.accountValues(_want) if _want else ib.accountValues()

            def _val(tag):
                for v in vals:
                    if v.tag == tag and (not _want or v.account == _want):
                        try:
                            return float(v.value)
                        except (TypeError, ValueError):
                            return None
                return None

            def _ccy(tag):
                for v in vals:
                    if v.tag == tag and (not _want or v.account == _want):
                        return v.currency or None
                return None

            # Today's executions (real fill prices). The clone's OPG/MOO orders
            # return BEFORE the auction fills, so the OMS otherwise has price=0;
            # ib.fills() carries the session's executed prices to reconcile.
            fills = []
            for f in ib.fills():
                ex = getattr(f, "execution", None)
                if ex is None:
                    continue
                if _want and (getattr(ex, "acctNumber", "") or "").strip() != _want:
                    continue
                try:
                    fills.append({
                        "symbol": f.contract.symbol,
                        "side": "BUY" if ex.side == "BOT" else "SELL",
                        "qty": abs(float(ex.shares)),
                        "price": float(ex.price),
                    })
                except (TypeError, ValueError, AttributeError):
                    continue

            # Account-level UnrealizedPnL is often absent from accountValues;
            # fall back to summing the position book so the row is never blank.
            unrl = _val("UnrealizedPnL")
            if unrl is None and positions:
                unrl = sum(p["unrealisedPnl"] for p in positions
                           if p.get("unrealisedPnl") is not None)

            # Broker-golden daily P&L. The OMS-computed realised is unreliable
            # for the clone (historical BUY fills are price 0 — ib.fills() clears
            # daily, so the basis can't be reconstructed). IBKR DOES know the
            # cost basis, so its reqPnL is the authoritative "today's P&L".
            daily_pnl = None
            try:
                pnl = ib.reqPnL(_want) if _want else None
                if pnl is not None:
                    await _aio.sleep(2.0)  # let the PnL subscription deliver
                    dp = getattr(pnl, "dailyPnL", None)
                    if dp is not None and dp == dp:  # not None / not NaN
                        daily_pnl = float(dp)
                    try:
                        ib.cancelPnL(_want)
                    except Exception:
                        pass
            except Exception:
                daily_pnl = None

            return {
                "broker": "IBKR_PAPER",
                "account_id": _want or None,
                "currency": _ccy("NetLiquidation"),
                "net_liquidation": _val("NetLiquidation"),
                "total_cash": _val("TotalCashValue"),
                "unrealised_pnl": unrl,
                "daily_pnl": daily_pnl,  # IBKR reqPnL — broker-golden today's P&L
                "positions": positions,
                "_fills": fills,
            }
        finally:
            ib.disconnect()

    # PREFER the central gateway's account-state snapshot — the gateway holds the
    # ONE connection (and now PLACES the orders, so its ib.fills() is the
    # authoritative session fills). Reading it means this desk opens NO broker
    # connection of its own, which is what removes the contention + the timeouts
    # that left the clone showing $0/idle. Direct read only if the gateway cache
    # is stale/missing.
    fills: list = []
    payload = None
    try:
        from ..ibkr_gateway import read_account_state
        gw = read_account_state()
    except Exception:  # noqa: BLE001
        gw = None
    if gw is not None:
        payload = {k: gw.get(k) for k in (
            "broker", "account_id", "currency", "net_liquidation",
            "total_cash", "unrealised_pnl", "daily_pnl", "positions")}
        fills = gw.get("fills") or []
        log.info("account-state: via CENTRAL gateway snapshot (no direct connection) "
                 "— NLV=%s, %d positions, %d fills",
                 payload.get("net_liquidation"), len(payload.get("positions") or []), len(fills))
    else:
        # Gateway cache stale/missing. PREFER the Web API (no :7500 dependency) —
        # this is THE path now that we've moved off the local Gateway. A direct
        # :7500 read is the last resort and only works if a Gateway is actually up
        # (it usually isn't — that's what left the clone showing $0/idle).
        try:
            payload = _fetch_ibkr_account_state_via_webapi(log)
        except Exception as exc:  # noqa: BLE001
            log.warning("account-state: web-api read failed (%s) — trying direct", exc)
            payload = None
        if payload is not None:
            fills = []  # session fills are Gateway-only; OMS reconcile covers prices
            log.info("account-state: via IBKR WEB API (no gateway) — NLV=%s, %d positions",
                     payload.get("net_liquidation"), len(payload.get("positions") or []))
        else:
            try:
                payload = _aio.run(_read())
                fills = payload.pop("_fills", [])
            except Exception as exc:  # noqa: BLE001
                log.warning("account-state: gateway cache stale, web-api unavailable "
                            "AND direct :7500 read failed (%s) — skip", exc)
                return
    try:
        import requests as _rq
        resp = _rq.post(
            f"{base.rstrip('/')}/api/ingest/account-state",
            headers={"Authorization": f"Bearer {token}"},
            json=payload, timeout=30)
        log.info("account-state push: HTTP %s — %s positions, NLV=%s %s",
                 resp.status_code, len(payload["positions"]),
                 payload.get("net_liquidation"), payload.get("currency"))
    except Exception as exc:  # noqa: BLE001
        log.warning("account-state push failed: %s", exc)

    # Reconcile real IBKR fill prices into the OMS. The clone's OPG orders are
    # recorded SUBMITTED with price 0 (placement returns before the auction); the
    # /fill endpoint stamps the real avg price + marks FILLED, so the clone's
    # Trades show real prices and per-trade P&L is no longer blind. Best-effort.
    if fills:
        try:
            _reconcile_ibkr_fills(fills, base, token, log)
        except Exception as exc:  # noqa: BLE001
            log.warning("ibkr fill-reconcile failed: %s", exc)


def _reconcile_ibkr_fills(fills, base: str, token: str, log) -> None:
    """Stamp real IBKR fill prices onto the clone's OMS orders. Matches each
    execution to an unpriced SUBMITTED order for ichimoku_equity_ibkr by
    (bare symbol, side); idempotent — orders already priced are skipped."""
    import requests as _rq
    H = {"Authorization": f"Bearer {token}"}
    rows = (_rq.get(f"{base.rstrip('/')}/api/oms/orders", headers=H, timeout=20)
            .json().get("orders", []))
    # Unpriced clone orders, newest-first, keyed by (symbol, side) for matching.
    open_by_key: dict[tuple, list] = {}
    for o in rows:
        if (o.get("strategyId") or "") != "ichimoku_equity_ibkr":
            continue
        if o.get("avgFillPrice"):  # already reconciled
            continue
        if (o.get("state") or "").upper() in ("CANCELLED", "REJECTED", "EXPIRED"):
            continue
        bare = str(o.get("symbol") or "").split(":")[-1].split(".")[0].upper()
        open_by_key.setdefault((bare, (o.get("side") or "").upper()), []).append(o)
    stamped = 0
    for fl in fills:
        bare = str(fl["symbol"]).split(":")[-1].split(".")[0].upper()
        bucket = open_by_key.get((bare, fl["side"]))
        if not bucket:
            continue
        o = bucket.pop(0)
        r = _rq.post(
            f"{base.rstrip('/')}/api/oms/orders/{o['id']}/fill", headers=H,
            json={"Qty": fl["qty"], "Price": fl["price"], "Fee": 0.0,
                  "Currency": "USD", "BrokerFillId": f"ibkr_recon:{bare}:{fl['side']}"},
            timeout=20)
        if r.status_code in (200, 201):
            stamped += 1
        elif r.status_code not in (409,):  # 409 = already filled, fine
            log.warning("fill-reconcile %s %s: HTTP %s %s",
                        bare, fl["side"], r.status_code, r.text[:120])
    if stamped:
        log.info("ibkr fill-reconcile: stamped %s real fill price(s) into OMS", stamped)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("tradepro.cli")
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if getattr(args, "from_config", False):
        _apply_config_overrides(args, log)
    # --broker is optional at parse time so --from-config can inject it from the
    # config row; enforce it here, once config has been applied.
    if not args.broker:
        raise SystemExit(
            "--broker is required (or use --from-config with a configured broker)")
    session_date = _resolve_session_date(args.date)
    # Yahoo / T212 / IG profiles require a session_date — when omitted
    # default to today's UTC date so the schedule-fired daemons
    # (paper-equity, paper-fx) Just Work without a --date flag.
    if session_date is None:
        session_date = datetime.utcnow().replace(microsecond=0)
        log.info("session_date defaulted to today UTC: %s", session_date.date())
    symbols = _resolve_symbols(args)
    # Per-broker instrument validation: drop universe names the target broker
    # can't actually trade (T212 lists ≠ IG ≠ IBKR) BEFORE they reach sizing /
    # the order router. Stops the WFRD/LFUS/JEF reject loop + phantom £0 fills.
    symbols = _validate_universe_against_broker(args, symbols)

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

    # Orphaned-exit fix: union the broker's HELD names into the universe so a
    # name that has rotated out of the sleeves but is still held keeps getting
    # bars + an exit evaluation (else it's stuck forever — e.g. NOW held at
    # -10% with no sell signal because it's no longer in the universe). Only
    # for a SINGLE, non-shared broker (T212 = all the equity strategy's own
    # positions); the shared IG account is skipped so we don't pull another
    # strategy's instruments into this one's book (the seed already guards
    # that, but the universe drives bars + entries too).
    if len(broker_list) == 1 and broker_list[0].lower() in ("t212", "ibkr"):
        held = _fetch_broker_held_symbols(broker_list[0])
        # SHARED-ACCOUNT GUARD: the IBKR paper account mixes the equity clone's and
        # FX clone's positions. Filter to THIS strategy's asset class so we never
        # union the other strategy's instruments (the FX-sold-META bug).
        held = _held_for_strategy_asset_class(held, getattr(args, "strategy", "") or "")
        existing = {s.upper() for s in symbols}
        added = [s for s in held if s.upper() not in existing]
        if added:
            symbols = symbols + added
            log.info(
                "orphaned-exit: unioned %d held name(s) into the universe for "
                "exit evaluation: %s", len(added), added,
            )

    # The bar bus delivers one trigger bar PER SYMBOL, and the multi-symbol
    # strategies (ichimoku_fx_mr 10 pairs, ichimoku_equity ~10-20 names)
    # decide per-symbol in on_bar — so a symbol with no trigger bar is
    # NEVER evaluated. The old `[:5]` cap therefore silently dropped half
    # the FX universe (USDCAD/NZDUSD/EURGBP/EURJPY/GBPJPY) and half the
    # equity names — the strategy ran on only its first 5 symbols, not the
    # user's full list. Raise the cap to cover real daemon universes; the
    # pathological huge-list case (the ~500-symbol intraday scan) is guarded
    # separately in the intraday engine (MAX_INTRADAY_SYMBOLS).
    # Cap exists so the bus doesn't fan out an unbounded per-symbol fetch and
    # get rate-limited. 170 covers the trader's full combined equity universe
    # (large_50 ∪ high_beta ≈ 163, + GLD) so all three sleeves' names are
    # traded, not an arbitrary slice. Daily bars are cached (CachedSource) so
    # after the first cold fetch each day the 15-min reruns hit cache; cold
    # fetches degrade gracefully (a 429-dropped symbol just refills next run).
    # The ~500-symbol intraday scan is still guarded by MAX_INTRADAY_SYMBOLS.
    _BUS_SYMBOL_CAP = 170
    bus_symbols = symbols if len(symbols) <= _BUS_SYMBOL_CAP else symbols[:_BUS_SYMBOL_CAP]

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
    seeded_avg_prices: dict[str, float] = {}
    if args.push:
        for b in broker_list:
            if not broker_requires_position_seed(b):
                continue  # sim broker — no persistent position to confirm
            try:
                seeded, seeded_avg = _seed_strategy_positions_from_broker(strategy, broker=b)
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
            seeded_avg_prices.update(seeded_avg)

    # Signal reconciliation: guarantee every name in `universe ∪ held` gets an
    # on_bar evaluation, even if the live feed delivered no trigger bar for it.
    # Thin/gappy names get no intraday bar → on_bar never runs → the signal is
    # SILENTLY DROPPED — a held name whose signal says EXIT never sells (STRL/STX/WDC)
    # AND a flat name whose signal says BUY never buys (the 8 missed buys). Root
    # cause + full reconciliation confirmed 2026-07-04. We wrap the bus so it emits
    # one synthetic bar per name AFTER the real bars, so on_bar evaluates BOTH the
    # exit (held) and the entry (flat) for every name.
    #
    # Priced from the broker mark (held) or the cache's latest daily close (universe)
    # so entry sizing is real. Parity-safe: the strategy's own entry gates + _moo_fired
    # dedup still apply — this only ensures on_bar RUNS; it never forces a trade the
    # signal/gates didn't already call for. Single non-shared broker only (T212 = the
    # equity strategy's OWN book; IG is shared; IBKR has native MOO).
    #
    # NOTE: this is a real behaviour change — it WILL open the missed-buy names that
    # signal long. Reviewed as such; entry logic unchanged, only its reachability.
    if seeded_positions and len(broker_list) == 1 and broker_list[0].lower() in ("t212", "ibkr"):
        from ..paper.bar_bus import HeldReconciliationBus
        from ..paper.strategy import Bar as _ReconBar
        _cache_dir = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
        marks = _fetch_broker_held_marks(broker_list[0])
        recon_px: dict[str, float] = {}
        # 1) held names → EXIT coverage, priced from the CACHE latest close FIRST.
        #    The cache close exists for every held name and is the SAME price that
        #    computes the daily signal, so every held name gets a synthetic bar and
        #    on_bar evaluates its exit deterministically. The broker mark is often
        #    None and the seeded avg can be 0 → those dropped held names to px=0,
        #    which line ~1712 filters out → the exit silently never fired (AMKR/STRL/
        #    AEIS were left held for days despite signal=SELL, while TTMI/STX/WDC that
        #    happened to have a price did exit). project_equity_exit_needs_trigger_bar.
        #    Long-only: skip (bug) shorts. Mark/avg kept only as last-resort fallback.
        _held = [_s for _s, _q in seeded_positions.items() if _q > 0]
        _held_closes = _latest_daily_closes(_held, _cache_dir)
        for _s in _held:
            recon_px[_s] = (
                _held_closes.get(_s) or marks.get(_s)
                or seeded_avg_prices.get(_s) or 0.0
            )
        # 2) universe names → cache latest close (ENTRY coverage). GATED behind
        #    --reconcile-entries (default OFF): this OPENS the missed-buy names, a real
        #    behaviour change, so it stays off until explicitly enabled after a review.
        #    When off, missed buys are still SURFACED by the signal-audit (observability),
        #    just not auto-traded.
        if getattr(args, "reconcile_entries", False):
            _uni = [s for s in bus_symbols if s not in recon_px]
            recon_px.update(_latest_daily_closes(_uni, _cache_dir))
        recon_bars = [
            _ReconBar(symbol=_s, timestamp=session_date, open=_px, high=_px, low=_px,
                      close=_px, volume=0, timeframe_seconds=86400, is_live=True)
            for _s, _px in recon_px.items() if _px > 0
        ]
        if recon_bars:
            bus = HeldReconciliationBus(inner=bus, reconciliation_bars=recon_bars)
            log.info(
                "signal reconciliation: wrapped bus with %d synthetic bar(s) "
                "(universe ∪ held) so every name gets an entry+exit evaluation",
                len(recon_bars))

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
        # Pass the broker cost basis so the ledger's unrealised P&L is REAL
        # (mark − avg) × qty — not mark × qty (≈ position value) which made
        # the cockpit P&L curve read ~$32k instead of the true +$284.
        engine.ledger.seed_positions(
            strategy.strategy_id, seeded_positions, avg_price=seeded_avg_prices or None)
        log.info(
            "LEDGER SEED: %s mirrored %d position(s), %d with cost basis",
            strategy.strategy_id, len(seeded_positions), len(seeded_avg_prices),
        )

    log.info(
        "Starting %s session: strategy=%s symbols=%s broker=%s interval=%s",
        args.strategy, strategy.strategy_id, symbols, args.broker, args.interval,
    )
    asyncio.run(engine.run(session_date or datetime.utcnow()))

    # Re-snapshot with recent fills so the Paper page Live tab renders
    # the per-strategy fill log + open positions.
    snapshot = engine.ledger.to_snapshot(include_fills=args.push_fills)
    # Re-mark open positions to the broker's LIVE price + recompute since-entry
    # P&L, so the displayed unrealised P&L can't be a stale daily close (the ANET
    # +$4.92-vs-real-$13.32 bug). Only when we actually hold something — a flat
    # session has nothing to re-mark and shouldn't make a broker round-trip.
    if any((b.get("positions") or []) for b in (snapshot.get("strategies") or [])):
        _overlay_live_marks_and_pnl(snapshot, args.broker, log)
    # Re-apply decisions / bars_seen / charts: ledger.to_snapshot doesn't
    # know about strategy instances, so the engine owns these side-
    # channels. attach_charts was missing here previously which is why
    # the cockpit's Strategy charts widget never populated after a
    # paper-session push — the engine ran recent_charts() inside its
    # own run() but those got dropped on this re-snapshot.
    engine.attach_decisions(snapshot)
    engine.attach_bars(snapshot)
    engine.attach_charts(snapshot)
    engine.attach_rejections(snapshot)
    snapshot["kind"] = "paper-snapshot"
    # Use strategy_id (not the strategy NAME) so a clone with a distinct
    # --strategy-id (e.g. ichimoku_equity_ibkr) gets its OWN snapshot label
    # instead of colliding with / clobbering the base strategy's. For the base
    # strategies strategy_id == name, so their labels are unchanged.
    snapshot["session_label"] = (
        f"{args.strategy_id or args.strategy}-{(session_date or datetime.utcnow()).date().isoformat()}"
    )
    snapshot["broker"] = args.broker
    snapshot["symbols"] = symbols
    print(json.dumps(snapshot, indent=2, default=str))

    if args.push:
        from . import push_to_api
        base, token = push_to_api.load_credentials()
        push_to_api.push("paper-snapshot", snapshot, base, token)
        # IBKR: also push the PAPER account's NLV / cash / P&L + position book
        # so the cockpit renders the algo clone's OWN account row + per-position
        # P&L. The live IBKRClient only sees IBKR_LIVE, so without this the clone
        # is invisible (£0/n.a). Best-effort — never fails the session.
        if args.broker.strip().lower() == "ibkr":
            _push_ibkr_account_state(getattr(args, "account", None), base, token, log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
