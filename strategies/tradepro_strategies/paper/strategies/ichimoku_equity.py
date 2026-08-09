"""IchimokuEquityStrategy — daily Ichimoku trend-following paper strategy.

Converts the quant_engine daily sleeve signals into live paper orders
via the TradePro paper engine.

SIGNAL FIDELITY — the long/flat signal is a VERBATIM port of the trader's
spec (docs/strategy.py IchimokuStrategy), kept in `_equity_trader_signal.py`
and pinned by `tests/test_equity_signal_parity.py`. The trader's logic, exactly:
  - Ichimoku params 5/32/50, 32-bar displacement (the trader's optimised set,
    not classic 9/26/52).
  - ENTRY: Close>cloud_top AND tenkan>kijun.  EXIT: Close<cloud_bottom OR
    tenkan<kijun.  STATEFUL (ffill) — a long is HELD while price sits inside
    the cloud; it only exits below cloud_bottom (the cloud thickness is the
    hold band). Long/flat only, never short.
(A prior version drifted: a stateless 1/0 recompute with an extra close>tenkan
entry condition, which dropped the hold-band and churned. Fixed 2026-06-03.)

Execution model (Market-on-Open):
  Signal computed ONCE at session start using prior-day cached daily data.
  -> Entry/exit order placed at the FIRST bar of the new session (MOO).
  -> Position held until the daily signal flips.

Regime gate:
  SPY < 200-SMA -> no new longs (AMBER/BEAR mode, existing positions hold
  until their own exit signal fires).

Vol-targeted sizing (trader-faithful, docs/portfolio 1.py):
  Each signalling name gets an equal weight 1/max(sleeve_size, n_signal) of
  sleeve capital (sleeve scaled ≤100% invested). The book is then scaled by ONE
  portfolio-level vol scalar — NOT per name:
      scalar   = min(max_leverage, target_vol / realised_vol_of_book)
      notional = per_name_capital × scalar
      qty      = floor(notional / price)   # NO max(1,…); sub-1-share → skip
  Applying vol-target to the aggregate (vs each name by its own vol) captures
  cross-name diversification, exactly as the trader's vectorised backtest does.

LLM signal gate (optional, fail_open by default):
  For each new ENTRY signal, the gate evaluates recent news sentiment.
  VETOED  -> order suppressed (advisory, not hard-stop).
  BOOSTED -> qty multiplied by scale_factor (typically 1.25).
  Exits are NEVER gated — you can always close a position.
  Pass `_llm_gate` in params to inject a pre-built LLMSignalGate for
  testing or manual construction without the strategy config registry.

Manual overrides (checked on every bar via OverrideRegistry):
  PAUSE         -> skip all signal generation this session
  VETO_ORDER    -> discard the pending order for this symbol (one-shot)
  PRICE_OVERRIDE -> convert MARKET to LIMIT at specified price (one-shot)
  SIZE_OVERRIDE  -> change qty before submission (one-shot; beats LLM scale)
  FORCE_CLOSE   -> emit opposing market order immediately (one-shot)

Injectable _data_fn for testing:
  Pass `_data_fn` in params to replace `ensure_cached` with a synthetic
  DataFrame supplier. Signature: _data_fn(symbol) -> pd.DataFrame | None
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..llm_gate import GateDecision, LLMSignalGate
from ..overrides import OverrideRegistry
from ..registry import register_strategy
from ..signal_bridge import realised_vol_from_closes
# The long/flat SIGNAL is a verbatim port of the trader's spec (docs/strategy.py),
# kept in _equity_trader_signal so it can't drift. Parity-tested.
from ._equity_trader_signal import latest_signal_and_meta, position_series, sleeve_weight
# Coarse, auditable sector buckets for the optional concentration cap
# (max_per_sector). Static table — see _equity_sectors for the rationale.
from ._equity_sectors import sector_for as _sector_for
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy
from ...gates.entry_quality import EntryQualityConfig, evaluate_entry_quality
from ...market_state import _volume_ratio_20d


def _daily_cached(kind: str, symbol: str, fetch_fn):
    """Disk-cache a per-symbol fetch for the CURRENT DAY. The gated clone runs a
    fresh process every 15 min (no in-memory carry), so without this it re-fetches
    sector-RS + Finnhub earnings for every signal name EVERY session → 429 rate-
    limits → retry storms → 2h sessions + a multi-GB log. RS + earnings dates don't
    change intraday, so one fetch per symbol per day is correct. Best-effort:
    any cache error just falls through to a live fetch (never blocks the signal)."""
    import os as _os, json as _json, datetime as _dt
    d = _os.path.expanduser(f"~/.tradepro/cache/{kind}")
    path = _os.path.join(d, f"{symbol}_{_dt.date.today().isoformat()}.json")
    try:
        if _os.path.exists(path):
            with open(path) as _f:
                return _json.load(_f)
    except Exception:  # noqa: BLE001
        pass
    val = fetch_fn()
    try:
        _os.makedirs(d, exist_ok=True)
        with open(path, "w") as _f:
            _json.dump(val, _f, default=str)
    except Exception:  # noqa: BLE001
        pass
    return val


def _resolve_api_base_light() -> str:
    """API base for the earnings-gate Finnhub/yfinance fetches. Env override →
    ~/.tradepro credentials (same box the pusher targets, which has FINNHUB_API_KEY)
    → localhost fallback. Kept light so importing this strategy never pulls compare."""
    import os as _os
    b = _os.environ.get("TRADEPRO_API_URL")
    if b:
        return b
    try:
        from ...cli.push_to_api import load_credentials
        base, _ = load_credentials()
        if base:
            return base
    except Exception:  # noqa: BLE001
        pass
    return "http://localhost:5080"


_log = logging.getLogger("tradepro.paper.ichimoku_equity")


@register_strategy("ichimoku_equity")
@dataclass
class IchimokuEquityStrategy(Strategy):
    """Daily Ichimoku trend-following with regime gate + vol-targeted sizing.

    One long-or-flat position per symbol. Signal recomputed once per
    session using cached daily history; entry/exit fires MOO on the
    first bar of the new session.
    """

    source = "trader-quant"
    caveats = [
        "FIDELITY: both the long/flat SIGNAL and the sleeve SIZING are verbatim "
        "ports of the trader's spec (docs/strategy.py + docs/portfolio.py), "
        "pinned by parity tests. Prior drift fixed 2026-06-03: (1) signal had "
        "an extra close>tenkan condition + was stateless (not the trader's "
        "stateful ffill hold through the cloud); (2) sizing used an invented "
        "top-N-by-conviction cap + flat capital/sleeve_size that over-deployed "
        ">100% when many names signalled — now each sleeve scales to ≤100% via "
        "weight = 1/max(sleeve_size, n_signal), holding every signalling name.",
        "Daily MOO entries (one per symbol per session) — designed for "
        "multi-week / multi-year holds, not intraday entries.",
        "Single-indicator (Ichimoku) trend filter — vulnerable to a "
        "regime shift from trend to range. Re-evaluate the signal on "
        "regime breaks (SPY < 200-SMA, vol spikes).",
        "Vol-target sizing uses the last 60d realised vol — fast vol "
        "spikes lag in the position sizer.",
    ]
    # Strategy reads daily history straight from the on-disk cache,
    # but the paper engine still needs at least one minute bar per
    # symbol for `on_bar` to fire and the MOO entry to be emitted.
    # default_lookback_days=1 ensures the previous trading day's
    # bars are fetched so triggering pre-market (before US open)
    # still produces a usable session.
    default_lookback_days = 1

    # Internal state (NOT in default_params — set in __post_init__).
    _positions: dict[str, int] = field(default_factory=dict)
    _daily_signals: dict[str, tuple[float, float, dict]] = field(default_factory=dict)
    _realised_vols: dict[str, float | None] = field(default_factory=dict)
    _moo_fired: set[str] = field(default_factory=set)
    # Set once per session the first time _regime_ok can't get a real read
    # (missing/NaN regime data) — stops one broken feed from spamming
    # run_log/log_decision once per symbol in the universe.
    _regime_issue_logged: bool = False
    # Names cleared for a NEW entry this session — the top-N-by-conviction
    # winners per sleeve. Empty set + no sleeves config = no cap (every
    # signalling name may enter, the pre-sleeve behaviour).
    _selected_entries: set[str] = field(default_factory=set)
    # Per-symbol capital for THIS session, computed from the trader's sleeve
    # weighting (1/max(sleeve_size, n_signal) × sleeve_capital) so each sleeve
    # is ≤100% invested no matter how many names signal. Overrides the static
    # per_symbol_capital. Set in _select_entries.
    _dynamic_capital: dict[str, float] = field(default_factory=dict)
    # Trailing daily closes per signalling name (tail(vol_lookback+1)), stashed
    # in _compute_signal so _select_entries can reconstruct the PORTFOLIO return
    # series for the single aggregate vol scalar (docs/portfolio 1.py).
    _closes_for_vol: dict[str, list[float]] = field(default_factory=dict)
    # ONE portfolio-level vol-target scalar for the whole book this session —
    # min(max_leverage, target_vol / realised_portfolio_vol). The trader applies
    # vol-target to the AGGREGATE return, NOT per name, so cross-name
    # diversification is captured. Set in _select_entries; 1.0 = no scaling.
    _vol_scalar: float = 1.0
    _overrides: OverrideRegistry | None = None
    _gate: LLMSignalGate | None = None

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "symbols": [],
            # Broker this strategy places through — drives broker-capability
            # gating (e.g. native MOO vs gate-at-open). None → treated as no
            # MOO support (gate applies), the safe default.
            "broker": None,
            "sleeve_size": 20,
            "capital_usd": 100_000.0,
            # Per-sleeve sizing: {symbol: capital_per_slot}. When set (by the
            # daemon's --sleeves), a name is sized by ITS sleeve's allocation
            # (the trader's large/hibeta/gold = 20/30/1, capital split equally
            # across sleeves) rather than one flat sleeve_size. None → flat.
            "per_symbol_capital": None,
            # Per-sleeve membership + slot counts for top-N-by-conviction
            # selection: {name: {"symbols": [...], "size": N}}. When set, each
            # session keeps held names then fills remaining slots with the
            # highest-conviction new candidates, so the number of positions —
            # and thus deployed capital — never exceeds the sleeve sizes (the
            # trader's fixed-slot model). None → no cap (every signalling name
            # may enter, the original behaviour).
            "sleeves": None,
            "tenkan": 5,
            "kijun": 32,
            "senkou_b": 50,
            "displacement": 32,
            "target_vol": 0.12,
            "max_leverage": 1.5,
            "vol_lookback": 60,
            "regime_sma_period": 200,
            "use_regime_filter": True,
            "regime_symbol": "SPY",
            "provider": "yahoo",
            "moo_window_bars": 1,
            "_data_fn": None,
            "_override_registry": None,
            # Injectable LLMSignalGate — set to a pre-built gate for tests
            # or leave None to disable the LLM layer (gate is opt-in here;
            # production uses StrategyRunner to build and inject it).
            "_llm_gate": None,
            # Phase C-3.2 — injectable CatalystFetcher that pulls active
            # catalysts from /api/catalysts/ before each gate.evaluate.
            # None ⇒ no catalyst overlay (legacy behaviour preserved).
            # Production wiring lives in StrategyRunner.
            "_catalyst_fetcher": None,
            # ── Optional risk controls (ALL OFF BY DEFAULT) ──────────────
            # These are switched ON only for a protected clone on a broker
            # that supports them. With every one left at its OFF default
            # (None), on_bar output is BYTE-FOR-BYTE identical to the legacy
            # T212 behaviour — proven by tests/test_equity_risk_controls.py.
            #
            # stop_loss_pct (float|None): if a HELD long's unrealised return
            #   is ≤ −stop_loss_pct (e.g. 0.08 ⇒ −8%), emit a SELL to FLATTEN
            #   that name ("stop"). Long-only: only SELL to close, never below
            #   zero. None ⇒ no stop. Requires a known cost basis
            #   (avg_entry_price > 0); a broker-seeded position with unknown
            #   entry is never stopped (can't compute a real % loss).
            "stop_loss_pct": None,
            # take_profit_pct (float|None): symmetric profit exit — if a held
            #   long's unrealised return is ≥ take_profit_pct, emit a SELL to
            #   flatten ("take-profit"). None ⇒ no take-profit. Same cost-
            #   basis requirement as the stop.
            "take_profit_pct": None,
            # max_per_sector (int|None): concentration cap on NEW entries —
            #   do not OPEN a name if its coarse sector (see _equity_sectors)
            #   already holds `max_per_sector` positions. Existing holdings
            #   are never touched. None ⇒ no cap.
            "max_per_sector": None,
            # entry_max_ext_pct (float|None): "don't chase" gate — SKIP a NEW
            #   long if Close is more than this % above its 200-day SMA (e.g.
            #   50 ⇒ block names >50% over the mean). Targets the blow-off-top
            #   entries that became the deepest losers (HPE +128%, CIEN +114%).
            #   None ⇒ no extension gate. Existing holdings/exits untouched.
            "entry_max_ext_pct": None,
            # entry_require_above_200sma (bool): PRIMARY-TREND floor (OPT-IN). When
            #   True, SKIP a NEW long whose Close is BELOW its own 200-day SMA. The
            #   Ichimoku cloud can flash long on a recovering dip that hasn't
            #   reclaimed its primary trend (TSLA bought $408 vs its 200-SMA $418).
            #   This is a DEVIATION from the trader's spec — his 200-SMA is a SPY
            #   MARKET-regime filter, not per-symbol — so it stays OFF here (verbatim)
            #   and is enabled ONLY on the clone. False/None ⇒ no-op. Entry only;
            #   never touches held positions or exits.
            "entry_require_above_200sma": False,
            # entry_veto_ma_suspect (bool): PENDING-M&A veto (OPT-IN). When True,
            #   SKIP a NEW long on a DEAL-PINNED name — a big 12m run now trading with
            #   COLLAPSED realized vol + pinned near its high (the WBD/Paramount case).
            #   Its "trend" is a deal ratchet: upside capped at the offer, downside a
            #   break-gap that won't fill at an ATR stop — a binary the trend/vol
            #   architecture can't see. NOT in the trader's spec (no M&A awareness), so
            #   OFF here (verbatim) and enabled on the clone only. Entry-gate only.
            "entry_veto_ma_suspect": False,
            # entry_rsi_max (float|None): SKIP a NEW long if RSI(14) > this
            #   (e.g. 75 ⇒ block overbought entries). None ⇒ no RSI gate.
            "entry_rsi_max": None,
            # entry_max_flips (int|None): "don't chop" / regime gate — SKIP a
            #   NEW long if the signal FLIPPED more than this many times in the
            #   last 40 bars (a choppy name that whipsaws the strategy — the
            #   diagnosed failure that lost 40/54 names even executed perfectly,
            #   e.g. KO churned 4 round-trips → −5%). None ⇒ no regime gate.
            #   Existing holdings/exits untouched.
            "entry_max_flips": None,
            # entry_fresh_only (bool): trade the DELTA, not the STATE. When True,
            #   only ENTER a new long that CROSSED to long on the LATEST bar — a
            #   fresh signal. A name long since a PRIOR day is a PAST signal and is
            #   SKIPPED (the entry window was then; buying now chases a stale/
            #   extended entry). Prevents importing the whole long backlog on a
            #   re-seed (the 62-buy fresh-start bug). Default True — restores the
            #   trader's trade-the-delta intent. In a continuous run every entry IS
            #   a fresh cross, so this is a no-op there (parity-safe); it only
            #   blocks entering an already-long name on a seed/re-seed.
            "entry_fresh_only": True,
            # entry_quality_gate (bool): OPT-IN. When True, SKIP a NEW long that is
            #   a relative-strength LAGGARD (rs_score < entry_min_rs) OR entering on
            #   THIN volume (volume_ratio_20d < entry_min_volume_ratio) — the ANET
            #   low-quality-entry case (rs 2/10 drifting up on 0.45x volume). OFF by
            #   default ⇒ verbatim/no-network parity. Vetoes the ENTRY only; held
            #   positions + exits are untouched. FAIL-OPEN: a MISSING input never
            #   blocks (only a confirmed floor breach vetoes), so a transient RS
            #   feed outage can't halt trading. RS needs a per-symbol sector-RS
            #   fetch, paid only when this gate is enabled.
            "entry_quality_gate": False,
            "entry_min_rs": 5.0,
            "entry_min_volume_ratio": 0.8,
            # entry_earnings_gate (bool): OPT-IN. When True, SKIP a NEW long that
            #   reports within the pre-earnings blackout (a binary print can gap it
            #   through the stop) or just reported (ATR still contaminated). OFF by
            #   default ⇒ verbatim/no-network parity. Vetoes the ENTRY only; held
            #   positions/exits untouched. FAIL-OPEN: a missing/failed earnings feed
            #   (UNKNOWN) never blocks. Needs a per-symbol earnings fetch (Finnhub
            #   upcoming + yfinance history via the API), paid only when enabled.
            "entry_earnings_gate": False,
            # entry_max_gap_pct (float|None): OPT-IN "don't chase the gap" cap. The
            #   signal fires on a daily CLOSE but the entry lands NEXT session at the
            #   live price — if that price is more than this % ABOVE the signal-bar
            #   close, the entry is chasing a gap (KO: signal $84.07 → entered $89.21
            #   = +6.1% into an earnings gap). SKIP it. None/OFF ⇒ verbatim parity.
            #   Distinct from entry_max_ext_pct (which is vs the 200-SMA, not the
            #   signal). Blocks the ENTRY only; a healthy pullback entry is unaffected.
            "entry_max_gap_pct": None,
        }

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def __post_init__(self) -> None:
        p = self._p()
        reg = p.get("_override_registry")
        if reg is None:
            # Module-level lazy singleton -- one registry per process,
            # shared across strategies that didn't explicitly inject one.
            reg = _default_registry()
        self._overrides = reg
        # LLM gate — use injected instance if provided (tests / runner).
        self._gate = p.get("_llm_gate") or None
        # Phase C-3.2 — optional CatalystFetcher (HTTP-backed lookup
        # with TTL cache; None ⇒ no overlay).
        self._catalyst_fetcher = p.get("_catalyst_fetcher") or None

    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        self._daily_signals.clear()
        self._realised_vols.clear()
        self._closes_for_vol.clear()
        self._vol_scalar = 1.0
        self._moo_fired.clear()
        self._regime_issue_logged = False
        # Pre-load positions from params.initial_positions so the
        # strategy knows what we ALREADY hold at the broker. Without
        # this, every session starts thinking it owns nothing → fires
        # BUY signals instead of HOLD/SELL on positions the broker
        # actually has.
        p = self._p()
        initial = p.get("initial_positions") or {}
        if isinstance(initial, dict):
            for sym, qty in initial.items():
                try:
                    self._positions[sym] = int(qty)
                except (TypeError, ValueError):
                    continue

        # Portfolio construction: rank this session's signalling names by
        # conviction and keep only the top-N per sleeve, so the number of
        # held positions — and thus deployed capital — can't exceed the
        # sleeve budgets. Runs AFTER positions are seeded so held names are
        # counted against their slots. No-op when no sleeves are configured.
        self._select_entries(p, session_date)

    def seed_positions(self, positions: dict[str, int],
                       avg_prices: dict[str, float] | None = None) -> None:
        """Called by paper_session._seed_strategy_positions_from_broker
        (Phase 2 of task #28). Pre-populates internal position state
        so the strategy doesn't re-emit entries for symbols it already
        holds. Symbols are bare tickers (AAPL); the daemon translates
        broker suffixes (AAPL_US_EQ) before calling.

        `avg_prices` (broker cost basis) is forwarded to super() so a
        seeded long gets a real avg_entry_price — without it the stop-
        loss / take-profit exits can't evaluate the seeded book and
        silently never fire (the bug behind the IBKR clone holding
        positions 10-19% underwater past its 8% stop).

        Calls super() so the base class also populates self.positions
        which is what the engine's risk gate reads — otherwise the
        gate sees position=0 and rejects SELL orders on held longs
        as 'short_disallowed' (#86).
        """
        super().seed_positions(positions, avg_prices)
        for sym, qty in positions.items():
            try:
                self._positions[sym] = int(qty)
            except (TypeError, ValueError):
                continue

    def on_bar(self, bar: Bar) -> list[Order]:
        p = self._p()
        sym = bar.symbol

        # Pause gate — short-circuit before any work.
        if self._overrides is not None and self._overrides.is_paused(self.strategy_id):
            self.log_decision(
                symbol=sym, bar_ts=bar.timestamp,
                action="skip-paused",
                reason="strategy is paused via overrides registry",
            )
            return []

        # Symbols whitelist.
        symbols = p.get("symbols") or []
        if symbols and sym not in symbols:
            # Non-whitelisted bars are routine noise — don't pollute
            # the decision trace with one entry per off-universe bar.
            return []

        # Force-close trumps signal logic (one-shot).
        if self._overrides is not None and self._overrides.consume_force_close(
            self.strategy_id, sym
        ):
            pos = self._positions.get(sym, 0)
            if pos != 0:
                side = OrderSide.SELL if pos > 0 else OrderSide.BUY
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="fire-force-close",
                    reason="override registry requested force-close",
                    qty=abs(pos), side=side.value, prior_position=pos,
                )
                return [Order(
                    strategy_id=self.strategy_id,
                    symbol=sym,
                    side=side,
                    quantity=abs(pos),
                    type=OrderType.MARKET,
                    tag=f"IchimokuEquity FORCE_CLOSE {sym} qty={abs(pos)}",
                )]
            self.log_decision(
                symbol=sym, bar_ts=bar.timestamp,
                action="skip-force-close-flat",
                reason="force-close requested but position already flat",
            )
            return []

        # Market-hours gate — don't emit into a CLOSED venue. ichimoku_equity
        # trades T212 US CASH equities, tradeable only during NYSE RTH. Outside
        # RTH (pre-market MOO, after-hours, weekends, holidays) the broker
        # rejects the order, and since the daemon re-runs every 15 min the same
        # order is regenerated + re-rejected on a loop. Skip when the symbol's
        # exchange calendar is closed. Operator force-close (above) is exempt.
        from datetime import datetime as _dt, timezone as _tz
        from .. import market_hours
        from ...bar_cache.asset_class_resolver import resolve_asset_class
        _ac = resolve_asset_class(sym)
        # Gate on the LIVE wall-clock, NOT bar.timestamp. This is a DAILY
        # strategy: its latest bar is stamped at the prior session's date
        # (midnight UTC), so is_open(bar.timestamp) is ALWAYS False and the
        # strategy would NEVER trade. The order is submitted NOW, so "is the
        # venue open" must be evaluated at now().
        #
        # We must apply this ONLY in a LIVE context, never in a historical
        # backtest/replay (which would skip every bar based on wall-clock).
        # The live t212 feed does NOT reliably set bar.is_live, so we ALSO
        # treat a RECENT bar (within a week of now — the live daemon always
        # feeds the latest close) as live. A historical replay's bars are old,
        # so they're never gated. (The vectorised backtest doesn't use on_bar.)
        _now = _dt.now(_tz.utc)
        # LIVE context = a REAL broker (not a sim/backtest), keyed off the broker
        # NOT bar recency. The old heuristic (bar.is_live or bar within 7d)
        # bypassed the gate whenever the live data feed went stale (yfinance
        # rate-limit) → the latest bar looked "old" → pre-market cancel-loop
        # returned (155 cancelled 2026-06-09). Broker-keying is robust: real
        # brokers always gate when their venue is closed; sims (replay/yfinance)
        # never gate, so the vectorised backtest/replay isn't wall-clock gated.
        _broker = (self._p().get("broker") or "").strip().lower()
        _live = _broker not in ("", "replay", "yfinance", "paper", "stub_live")
        # Broker-capability gate: brokers with a NATIVE MOO/opening-auction order
        # (IBKR/Alpaca) accept the order pre-market — it queues for the open — so
        # we DON'T gate them. Brokers without it (T212) must hold until the venue
        # opens, else the pre-market market order is rejected + re-emitted (the
        # cancel-loop). Config-driven per broker (broker_capabilities).
        from ... import broker_capabilities
        _moo = broker_capabilities.supports_moo(_broker)
        if _live and (not _moo) and not market_hours.is_open(_ac, _now):
            self.log_decision(
                symbol=sym, bar_ts=bar.timestamp,
                action="skip-market-closed",
                reason=f"{_ac} venue closed now ({_dt.now(_tz.utc).isoformat()}) — "
                       f"not emitting (broker would reject + re-cancel each run)",
            )
            return []

        # MOO model: at most one decision per symbol per session.
        if sym in self._moo_fired:
            return []
        self._moo_fired.add(sym)

        # Compute signal + realised vol.
        signal, vol, meta = self._compute_signal(sym, bar, p)

        # Veto suppresses the resulting order (one-shot).
        if self._overrides is not None and self._overrides.consume_veto(
            self.strategy_id, sym
        ):
            self.log_decision(
                symbol=sym, bar_ts=bar.timestamp,
                action="skip-vetoed",
                reason="override registry vetoed this signal",
                signal=signal,
            )
            return []

        position = self._positions.get(sym, 0)
        cloud_pos = meta.get("cloud_position", "?") if meta else "?"

        # ── Risk-control exits: stop-loss / take-profit (OPT-IN) ─────────
        # OFF by default (both params None) ⇒ this block is a no-op and
        # on_bar behaves exactly as before. When enabled (protected clone),
        # a held long that breaches its stop or take-profit is FLATTENED
        # here, BEFORE the signal-based entry/exit logic — a hard risk
        # exit always wins over the Ichimoku signal. Long-only: we only
        # ever SELL to close, never below zero (position > 0 guard).
        #
        # Subject to the SAME market-hours gate above (it precedes this) —
        # we never emit a risk exit into a closed venue. Subject to the
        # SAME once-per-session MOO model (one decision per symbol/session).
        risk_exit = self._risk_exit_order(sym, bar, p, position)
        if risk_exit is not None:
            return [risk_exit]

        # Long entry.
        if signal >= 1.0 and position == 0:
            # ── Fresh-signal gate: trade the DELTA, not the STATE (OPT-IN) ──
            # Only ENTER on a long that CROSSED on the latest bar. A name long
            # since a PRIOR day is a PAST signal — the entry window was then;
            # buying now chases a stale (often extended) entry, and on a re-seed
            # imports the whole accumulated backlog (the 62-buy fresh-start bug).
            # Restores the trader's trade-the-delta intent. Blocks the ENTRY only.
            if p.get("entry_fresh_only") and not (meta and meta.get("long_fresh")):
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="skip-stale-signal",
                    reason=("long signal is not a fresh cross (long since a prior "
                            "day) — trade-the-delta; don't chase past signals"),
                    signal=signal, cloud_position=cloud_pos,
                )
                return []
            # ── Entry-extension "don't chase" gate (OPT-IN) ─────────────────
            # OFF by default (both None) ⇒ no-op. When set, SKIP a NEW long
            # that is already over-extended at entry — the diagnosed failure
            # mode where the book bought blow-off tops (median loser +42% over
            # its 200-SMA; HPE entered RSI 92 / +128% over → −19%). Blocks the
            # ENTRY only; never touches held positions or exits.
            ext_max = p.get("entry_max_ext_pct")
            rsi_max = p.get("entry_rsi_max")
            if ext_max is not None or rsi_max is not None:
                ext = meta.get("ext_pct") if meta else None
                rsi = meta.get("rsi") if meta else None
                why = None
                if ext_max is not None and ext is not None and ext > float(ext_max):
                    why = f"{ext:+.0f}% over 200-SMA > {ext_max}% cap — too extended"
                elif rsi_max is not None and rsi is not None and rsi > float(rsi_max):
                    why = f"RSI {rsi:.0f} > {rsi_max} cap — overbought"
                if why is not None:
                    self.log_decision(
                        symbol=sym, bar_ts=bar.timestamp,
                        action="skip-extended",
                        reason=f"don't-chase gate: {why}",
                        signal=signal, cloud_position=cloud_pos,
                    )
                    return []
            # ── Entry-quality gate: RS + volume floors (OPT-IN) ─────────────
            # OFF by default ⇒ no-op (verbatim parity). When on, SKIP a NEW long
            # that is a relative-strength LAGGARD OR entering on THIN volume — the
            # ANET case (rs 2/10 on 0.45x volume). FAIL-OPEN: only a confirmed floor
            # breach (action == "veto") blocks; a missing RS/volume input never
            # halts the entry. Blocks the ENTRY only; held positions/exits untouched.
            if p.get("entry_quality_gate"):
                _eq = evaluate_entry_quality(
                    rs_score=(meta.get("rs_score") if meta else None),
                    volume_ratio=(meta.get("volume_ratio_20d") if meta else None),
                    cfg=EntryQualityConfig(
                        min_rs_score=float(p.get("entry_min_rs", 5.0)),
                        min_volume_ratio=float(p.get("entry_min_volume_ratio", 0.8)),
                    ),
                )
                if _eq.action == "veto":
                    self.log_decision(
                        symbol=sym, bar_ts=bar.timestamp,
                        action="skip-low-quality-entry",
                        reason=f"entry-quality gate: {_eq.to_dict()['summary']}",
                        signal=signal, cloud_position=cloud_pos,
                    )
                    return []
            # ── Earnings-proximity gate (OPT-IN) ────────────────────────────
            # OFF by default ⇒ no-op. When on, SKIP a NEW long reporting within the
            # pre-earnings blackout (a binary print can gap it through the stop) or
            # just-reported. FAIL-OPEN: only a confirmed blackout (earnings_veto)
            # blocks; a missing feed (UNKNOWN) never halts. Blocks the ENTRY only.
            if p.get("entry_earnings_gate") and meta and meta.get("earnings_veto"):
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="skip-earnings-blackout",
                    reason=f"earnings gate: {meta.get('earnings_reason', 'earnings proximity')}",
                    signal=signal, cloud_position=cloud_pos,
                )
                return []
            # ── "Don't chase the gap" cap: entry vs the SIGNAL price (OPT-IN) ─
            # OFF by default (None) ⇒ no-op. The signal fires on a daily CLOSE but
            # the entry lands NEXT session at the live price — if that price gapped
            # more than entry_max_gap_pct ABOVE the signal close, we're chasing (KO:
            # $84.07 signal → $89.21 entry = +6.1% into an earnings gap). SKIP it.
            # Fail-open: no signal_close (thin history) ⇒ don't block. Entry only.
            gap_max = p.get("entry_max_gap_pct")
            if gap_max is not None and meta:
                sig_px = meta.get("signal_close")
                if sig_px and sig_px > 0 and bar.close > 0:
                    gap_pct = (float(bar.close) / float(sig_px) - 1.0) * 100.0
                    if gap_pct > float(gap_max):
                        self.log_decision(
                            symbol=sym, bar_ts=bar.timestamp,
                            action="skip-gap-chase",
                            reason=(f"entry {bar.close:,.2f} is {gap_pct:+.1f}% above the "
                                    f"signal price {sig_px:,.2f} (> {gap_max}% cap) — chasing a gap"),
                            signal=signal, cloud_position=cloud_pos,
                        )
                        return []
            # ── Primary-trend "must be above its own 200-SMA" floor (OPT-IN) ─
            # OFF by default ⇒ no-op (verbatim spec). When True, SKIP a NEW long
            # BELOW its own 200-day SMA — a primary trend not yet reclaimed (the
            # TSLA-below-$418 case). Reuses the precomputed ext_pct (% over 200-SMA;
            # <0 = below). Fail-open when ext is None (<200 bars — don't fabricate a
            # gate). A DEVIATION from the trader's spec → enabled on the clone only.
            if p.get("entry_require_above_200sma"):
                ext = meta.get("ext_pct") if meta else None
                if ext is not None and ext < 0:
                    self.log_decision(
                        symbol=sym, bar_ts=bar.timestamp,
                        action="skip-below-200sma",
                        reason=(f"{ext:+.1f}% vs its 200-SMA (below) — primary trend "
                                f"not reclaimed; standing aside (require_above_200sma)"),
                        signal=signal, cloud_position=cloud_pos,
                    )
                    return []
            # ── Pending-M&A veto (OPT-IN, clone deviation) ──────────────────
            # OFF by default ⇒ no-op (verbatim spec). When True, SKIP a NEW long on a
            # DEAL-PINNED name (WBD/Paramount): perfect trend score, but a deal ratchet
            # — upside capped at the offer, downside a break-gap that won't fill at an
            # ATR stop. The trader's spec has no M&A awareness → clone-only. Entry only.
            if p.get("entry_veto_ma_suspect") and meta and meta.get("ma_deal_suspect"):
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="skip-ma-deal",
                    reason=f"pending-M&A veto: {meta.get('ma_deal_reason', 'deal-pinned, not a trend')}",
                    signal=signal, cloud_position=cloud_pos,
                )
                return []
            # ── Regime / anti-whipsaw "don't chop" gate (OPT-IN) ────────────
            # OFF by default (None) ⇒ no-op. When set, SKIP a NEW long on a
            # CHOPPY name — one whose signal flipped > entry_max_flips times in
            # the last 40 bars. Choppy names churn the strategy (buy-high/
            # sell-low round-trips) and were the dominant loss (40/54 names lost
            # even executed perfectly). Blocks the ENTRY only; holdings/exits
            # untouched.
            flips_max = p.get("entry_max_flips")
            if flips_max is not None:
                flips = meta.get("recent_flips") if meta else None
                if flips is not None and flips > int(flips_max):
                    self.log_decision(
                        symbol=sym, bar_ts=bar.timestamp,
                        action="skip-choppy",
                        reason=(
                            f"regime gate: {flips} signal flips in ~40 bars > "
                            f"{int(flips_max)} — choppy, whipsaw risk; standing aside"
                        ),
                        signal=signal, cloud_position=cloud_pos,
                    )
                    return []
            # Top-N-by-conviction gate: when sleeves are configured, only the
            # winners selected at session start may enter. A signalling name
            # that didn't make its sleeve's slot cut is dropped this session
            # so capital goes to the strongest names (the trader's fixed-slot
            # model). Empty set = no sleeves configured = no cap.
            if self._selected_entries and sym not in self._selected_entries:
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="skip-not-selected",
                    reason=(
                        "signal fired but ranked below the sleeve's top-N by "
                        "conviction — capital allocated to stronger names this session"
                    ),
                    signal=signal, cloud_position=cloud_pos,
                )
                return []
            # ── Sector concentration cap (OPT-IN) ───────────────────────
            # OFF by default (max_per_sector=None) ⇒ no-op. When set, do NOT
            # open a NEW name if its coarse sector already holds the cap.
            # Applies ONLY to new entries — never touches existing holdings,
            # never forces an exit. Skip-with-reason so a 0-fill on a
            # signalling name is explainable in the decision trace.
            cap = p.get("max_per_sector")
            if cap is not None:
                sec = _sector_for(sym)
                held = self._sector_held_count(sec, exclude=sym)
                if held >= int(cap):
                    self.log_decision(
                        symbol=sym, bar_ts=bar.timestamp,
                        action="skip-sector-cap",
                        reason=(
                            f"sector '{sec}' already holds {held} position(s) "
                            f"(max_per_sector={int(cap)}) — new entry skipped to "
                            f"cap concentration; existing holdings untouched"
                        ),
                        signal=signal, cloud_position=cloud_pos,
                        sector=sec, sector_held=held, max_per_sector=int(cap),
                    )
                    return []
            # ── LLM signal gate ─────────────────────────────────────────
            # Runs BEFORE sizing so BOOSTED decisions can scale the qty.
            # The gate is advisory (fail_open=True default): an LLM error
            # never blocks trading. Exits are never gated — we can always
            # close a position regardless of news sentiment.
            # Phase C-3.2 — pass catalysts only when a fetcher was
            # injected. The no-fetcher path keeps the legacy 2-arg call
            # signature so existing gate stubs in tests + third-party
            # mocks don't trip on a new kwarg.
            if self._gate is None:
                gate_decision = GateDecision(
                    action=GateDecision.APPROVED, scale_factor=1.0,
                    reason="no gate configured",
                )
            elif self._catalyst_fetcher is not None:
                gate_decision = self._gate.evaluate(
                    sym, signal,
                    catalysts=self._catalyst_fetcher.fetch(sym),
                )
            else:
                gate_decision = self._gate.evaluate(sym, signal)
            if gate_decision.action == GateDecision.VETOED:
                _log.info(
                    "IchimokuEquity LLM gate VETOED %s: %s", sym, gate_decision.reason
                )
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="skip-llm-vetoed",
                    reason=f"LLM gate vetoed: {gate_decision.reason}",
                    signal=signal, cloud_position=cloud_pos,
                )
                return []
            # scale_factor = 1.0 (normal) or >1.0 (boosted)
            llm_scale = gate_decision.scale_factor
            # ────────────────────────────────────────────────────────────

            # Trader-faithful sizing (docs/portfolio 1.py): per-name notional =
            # capital weight × the ONE portfolio vol scalar; whole-share floor
            # with NO max(1,…). A name whose fractional target rounds below 1
            # share is SKIPPED (preserves the ≤100%-invested weight discipline)
            # rather than force-bought at a 1-share position the trader never
            # sized — that floor previously over-risked expensive volatile names.
            per_name_cap = self._capital_per_slot(sym, p)
            notional = per_name_cap * self._vol_scalar
            qty = int(notional // bar.close) if bar.close > 0 else 0
            # Apply LLM boost to algo-computed size.
            qty = int(qty * llm_scale)

            # Human size override beats LLM scaling (explicit trader intent).
            if self._overrides is not None:
                size_ov = self._overrides.get_size_override(self.strategy_id, sym)
                if size_ov is not None and size_ov > 0:
                    qty = size_ov
                price_ov = self._overrides.get_price_override(self.strategy_id, sym)
            else:
                price_ov = None

            if qty <= 0:
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp,
                    action="skip-sub-share",
                    reason=(
                        f"fractional target {self._capital_per_slot(sym, p) * self._vol_scalar:,.0f}$"
                        f" / {float(bar.close):,.2f} < 1 share — skipped (trader weights "
                        f"this name <1 share; not force-bought at a 1-share over-risk)"
                    ),
                    signal=signal, vol=vol, price=float(bar.close),
                    vol_scalar=self._vol_scalar,
                )
                return []

            gate_tag = (
                f" llm={gate_decision.action}"
                if gate_decision.action != GateDecision.APPROVED
                else ""
            )
            tag = (
                f"IchimokuEquity MOO entry {sym} "
                f"signal=1 cloud={cloud_pos} vol={vol:.3f}{gate_tag}"
                if vol is not None
                else f"IchimokuEquity MOO entry {sym} signal=1 cloud={cloud_pos}{gate_tag}"
            )

            self.log_decision(
                symbol=sym, bar_ts=bar.timestamp,
                action="fire-moo-entry",
                reason=(
                    f"signal=1 (close>cloud_top, tenkan>kijun, close>tenkan) "
                    f"cloud={cloud_pos}"
                ),
                qty=qty, side="BUY", signal=signal,
                vol=vol, llm_scale=llm_scale,
                cloud_position=cloud_pos,
                order_type="LMT" if price_ov is not None else "MKT",
                limit_price=float(price_ov) if price_ov is not None else None,
            )

            if price_ov is not None:
                return [Order(
                    strategy_id=self.strategy_id,
                    symbol=sym,
                    side=OrderSide.BUY,
                    quantity=qty,
                    type=OrderType.LIMIT,
                    limit_price=float(price_ov),
                    tag=tag + f" LIMIT@{price_ov:.2f}",
                )]
            return [Order(
                strategy_id=self.strategy_id,
                symbol=sym,
                side=OrderSide.BUY,
                quantity=qty,
                type=OrderType.MARKET,
                tag=tag,
            )]

        # Long exit — NEVER gated; always execute.
        if signal < 1.0 and position > 0:
            self.log_decision(
                symbol=sym, bar_ts=bar.timestamp,
                action="fire-moo-exit",
                reason=f"signal=0 with open long position {position}",
                qty=position, side="SELL", signal=signal,
                cloud_position=cloud_pos, prior_position=position,
            )
            return [Order(
                strategy_id=self.strategy_id,
                symbol=sym,
                side=OrderSide.SELL,
                quantity=position,
                type=OrderType.MARKET,
                tag=f"IchimokuEquity MOO exit {sym} signal=0",
            )]

        # Reached after the MOO gate but no entry / exit fired. Record
        # the reason so a 0-fill session is explainable: cloud unfavour-
        # able, already-long, no signal flip, etc.
        if signal >= 1.0 and position > 0:
            reason = f"signal=1 but already long {position}"
            action_label = "skip-already-long"
        elif signal < 1.0 and position == 0:
            reason = f"signal=0 and flat (waiting for cloud-bull setup) cloud={cloud_pos}"
            action_label = "skip-flat-no-signal"
        else:
            reason = f"signal={signal} cloud={cloud_pos} no MOO action"
            action_label = "skip-no-action"
        self.log_decision(
            symbol=sym, bar_ts=bar.timestamp,
            action=action_label, reason=reason,
            signal=signal, position=position,
            cloud_position=cloud_pos, vol=vol,
        )

        return []

    def on_fill(self, fill: Fill) -> None:
        prev = self._positions.get(fill.symbol, 0)
        if fill.side == OrderSide.BUY:
            self._positions[fill.symbol] = prev + fill.quantity
        else:
            self._positions[fill.symbol] = prev - fill.quantity

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        held = {s: q for s, q in self._positions.items() if q != 0}
        if held:
            _log.info(
                "IchimokuEquity session_end holdings: %s",
                ", ".join(f"{s}={q}" for s, q in held.items()),
            )

    def recent_charts(self) -> dict[str, dict[str, Any]]:  # type: ignore[override]
        """Emit a per-symbol Ichimoku-cloud chart so the trader can
        validate "where did the signal actually fire vs where the
        cloud was?" on /paper-live/session/<id>. Errors per-symbol
        are caught locally so one broken symbol doesn't strip charts
        from the other three."""
        from ...viz import build_chart

        p = self._p()
        out: dict[str, dict[str, Any]] = {}
        for sym in p.get("symbols") or list(self._daily_signals.keys()):
            try:
                df = self._fetch_df(sym, p)
                if df is None or df.empty:
                    continue
                # Pull this strategy's own fills for the symbol so the
                # chart can render BUY/SELL markers exactly where the
                # MOO entry executed. Engine.record_fill (called before
                # on_fill) populates self._fills_seen — no engine-
                # ledger coupling needed.
                fills_for_sym = self.recent_fills(symbol=sym)
                out[f"ichimoku_cloud:{sym}"] = build_chart(
                    "ichimoku_cloud",
                    symbol=sym,
                    df=df,
                    fills=fills_for_sym,
                    tenkan=p["tenkan"],
                    kijun=p["kijun"],
                    senkou_b=p["senkou_b"],
                    displacement=p["displacement"],
                )
            except Exception:  # noqa: BLE001
                _log.exception(
                    "ichimoku_equity recent_charts failed for %s — skipping",
                    sym,
                )
        return out

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _p(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}

    def _sector_held_count(self, sector: str, *, exclude: str | None = None) -> int:
        """Number of CURRENTLY-HELD long positions in `sector`.

        Reads the strategy's own position state (`_positions`); only long
        (qty > 0) names count toward the concentration cap. `exclude` drops
        the candidate symbol itself so it never counts against its own
        entry. Pure read — never mutates state."""
        count = 0
        for sym, qty in self._positions.items():
            if qty <= 0 or sym == exclude:
                continue
            if _sector_for(sym) == sector:
                count += 1
        return count

    def _risk_exit_order(
        self, sym: str, bar: Bar, p: dict[str, Any], position: int,
    ) -> Order | None:
        """Stop-loss / take-profit exit for a held long (OPT-IN).

        Returns a SELL Order to FLATTEN the position when an enabled risk
        threshold is breached, else None. Both params None (the default) ⇒
        always None ⇒ ZERO behaviour change. Long-only: only fires for a
        long (position > 0) and only ever SELLs to close — never short.

        Needs a real cost basis: unrealised % is computed against
        `position_for(sym).avg_entry_price` (maintained by the engine on
        every fill). A broker-seeded position with unknown entry
        (avg_entry_price <= 0) is NEVER stopped/taken — we can't compute a
        genuine loss/gain and won't act on a fabricated one."""
        stop_pct = p.get("stop_loss_pct")
        tp_pct = p.get("take_profit_pct")
        if stop_pct is None and tp_pct is None:
            return None  # both controls OFF — legacy behaviour, no-op.
        if position <= 0:
            return None  # long-only: nothing to stop/take on a flat/short book.

        entry = float(self.position_for(sym).avg_entry_price or 0.0)
        if entry <= 0:
            # Unknown cost basis (e.g. broker-seeded leftover) — cannot
            # compute a real unrealised %, so we do NOT risk-exit on a
            # fabricated number. Signal logic still applies downstream.
            return None
        mark = float(bar.close)
        if mark <= 0:
            return None
        unreal_pct = (mark - entry) / entry

        kind: str | None = None
        if stop_pct is not None and unreal_pct <= -abs(float(stop_pct)):
            kind = "stop"
        elif tp_pct is not None and unreal_pct >= abs(float(tp_pct)):
            kind = "take-profit"
        if kind is None:
            return None

        reason = (
            f"{kind}: unrealised {unreal_pct:+.2%} vs entry {entry:,.2f} "
            f"(mark {mark:,.2f}) breached "
            + (f"stop −{abs(float(stop_pct)):.2%}" if kind == "stop"
               else f"target +{abs(float(tp_pct)):.2%}")
            + " — flattening long (sell-to-close only, never short)"
        )
        self.log_decision(
            symbol=sym, bar_ts=bar.timestamp,
            action=f"fire-{kind}",
            reason=reason,
            qty=position, side="SELL", prior_position=position,
            entry_price=entry, mark=mark, unrealised_pct=unreal_pct,
            stop_loss_pct=stop_pct, take_profit_pct=tp_pct,
        )
        return Order(
            strategy_id=self.strategy_id,
            symbol=sym,
            side=OrderSide.SELL,
            quantity=position,
            type=OrderType.MARKET,
            tag=(
                f"IchimokuEquity {kind.upper()} {sym} "
                f"unreal={unreal_pct:+.2%} qty={position}"
            ),
        )

    def _capital_per_slot(self, sym: str, p: dict[str, Any]) -> float:
        """Capital allocated to one position before vol-scaling.

        Per-sleeve sizing (the trader's model): when the daemon passes
        `per_symbol_capital` = {symbol: sleeve_capital/sleeve_size}, each name
        is sized by ITS sleeve (large/hibeta/gold = 20/30/1, capital split
        equally across sleeves). Falls back to the flat capital_usd/sleeve_size
        for any symbol without a per-sleeve allocation (back-compat)."""
        # Trader-faithful dynamic allocation (set in _select_entries): each
        # sleeve scaled to ≤100% invested via 1/max(sleeve_size, n_signal).
        if sym in self._dynamic_capital:
            return float(self._dynamic_capital[sym])
        psc = p.get("per_symbol_capital") or {}
        if sym in psc:
            return float(psc[sym])
        return float(p["capital_usd"]) / max(1, int(p.get("sleeve_size", 20)))

    def _select_entries(self, p: dict[str, Any], session_date) -> None:
        """Trader-faithful sleeve sizing (docs/portfolio 1.py). For each sleeve
        we HOLD EVERY signalling name (no top-N cut — the trader doesn't have
        one) and size each at the trader's weight:

            weight = 1 / max(sleeve_size, n_signal)   (sleeve scaled ≤100%)
            per-name capital = weight × (capital / n_sleeves)

        So as more names signal, each gets smaller and the sleeve stays ≤100%
        invested — instead of the old top-N-by-conviction cap (an invented
        divergence) + flat capital/sleeve_size sizing that over-deployed and
        exhausted cash when >sleeve_size names signalled. The per-name capital
        goes into `_dynamic_capital` (read by `_capital_per_slot`).

        No `sleeves` config → `_selected_entries`/`_dynamic_capital` stay empty
        and on_bar treats that as 'no cap / flat sizing' (original behaviour)."""
        self._selected_entries = set()
        self._dynamic_capital = {}
        sleeves = p.get("sleeves")
        if not sleeves:
            return

        capital = float(p["capital_usd"])
        sleeve_capital = capital / max(1, len(sleeves))

        for name, spec in sleeves.items():
            syms = spec.get("symbols") or []
            size = int(spec.get("size", len(syms)))
            # Every name in the sleeve that signals long this session.
            signalling = [
                sym for sym in syms
                if self._compute_signal(sym, None, p)[0] >= 1.0
            ]
            n_signal = len(signalling)
            weight = sleeve_weight(n_signal, size)
            per_name_cap = weight * sleeve_capital
            for sym in signalling:
                self._dynamic_capital[sym] = per_name_cap
                # Names not already held are eligible to ENTER; held names that
                # still signal keep their position (on_bar holds them). Held
                # names that stopped signalling aren't here → on_bar exits them.
                if self._positions.get(sym, 0) <= 0:
                    self._selected_entries.add(sym)
            self.log_decision(
                symbol=f"sleeve:{name}", bar_ts=session_date,
                action="sleeve-selection",
                reason=(
                    f"sleeve '{name}': {n_signal} signalling names, each weighted "
                    f"1/max({size},{n_signal}) ⇒ {weight:.4f} of sleeve capital "
                    f"(${per_name_cap:,.0f}/name); sleeve ≤100% invested "
                    f"(trader's scale-to-100, no top-N cut)"
                ),
                sleeve=name, slots=size, n_signal=n_signal,
                weight=weight, per_name_capital=per_name_cap,
            )

        # ── Portfolio-level vol target (docs/portfolio 1.py:99-112) ──────────
        # The trader scales the AGGREGATE book return by ONE scalar, not each
        # name by its own vol. Reconstruct the trailing portfolio return from
        # each name's overall weight (per_name_cap / capital) × its daily log
        # returns, then scalar = min(max_leverage, target_vol / realised_vol).
        # Captures diversification — per-name vol-targeting (the old, drifted
        # behaviour) does not and over-shrinks individually-volatile names.
        self._vol_scalar = self._portfolio_vol_scalar(p, capital, session_date)

    def _portfolio_vol_scalar(
        self, p: dict[str, Any], capital: float, session_date,
    ) -> float:
        """One vol-target scalar for the whole book — the trader's
        `_apply_vol_target` (docs/portfolio 1.py) ported to streaming.

        realised_vol = std(Σ wᵢ·log_returnᵢ) × √252  over the trailing window;
        scalar = min(max_leverage, target_vol / realised_vol), default 1.0 when
        there isn't enough history (mirrors the trader's `.fillna(1.0)`)."""
        target_vol = float(p["target_vol"])
        max_lev = float(p["max_leverage"])
        if capital <= 0 or not self._dynamic_capital:
            return 1.0

        series: list[tuple[float, np.ndarray]] = []
        for sym, cap in self._dynamic_capital.items():
            closes = self._closes_for_vol.get(sym)
            if not closes or len(closes) < 21:  # < 20 returns → too noisy
                continue
            arr = np.asarray(closes, dtype=float)
            if np.any(arr <= 0):
                continue
            rets = np.diff(np.log(arr))
            series.append((cap / capital, rets))  # overall portfolio weight

        if not series:
            return 1.0
        n = min(len(r) for _, r in series)
        if n < 20:
            return 1.0
        port = np.zeros(n)
        for w, rets in series:
            port += w * rets[-n:]
        realised_vol = float(np.std(port, ddof=1)) * np.sqrt(252.0)
        scalar = 1.0 if realised_vol <= 0 else min(max_lev, target_vol / realised_vol)

        self.log_decision(
            symbol="portfolio:vol-target", bar_ts=session_date,
            action="vol-target",
            reason=(
                f"book realised vol {realised_vol:.1%} (ann.) over {n}d vs target "
                f"{target_vol:.0%} ⇒ ONE scalar {scalar:.3f} (capped at "
                f"{max_lev}×) applied to every name — trader's aggregate "
                f"vol-target, not per-name"
            ),
            realised_vol=realised_vol, target_vol=target_vol,
            scalar=scalar, names=len(series),
        )
        return scalar

    def _fetch_df(self, symbol: str, p: dict[str, Any]) -> pd.DataFrame | None:
        """Pluggable data lookup. Tests inject `_data_fn`; production
        falls back to the on-disk cache (no live network call here)."""
        fn: Callable[[str], pd.DataFrame | None] | None = p.get("_data_fn")
        if fn is not None:
            try:
                return fn(symbol)
            except Exception as exc:  # noqa: BLE001
                _log.debug("IchimokuEquity _data_fn failed for %s: %s", symbol, exc)
                return None

        # IBKR-golden-source path first (ibkr_web -> ibkr -> ig -> yfinance),
        # legacy yahoo cache as fallback only — see [[feedback_ibkr_golden_source_yahoo_fallback]].
        from ...ibkr_bars import fetch_daily_bars
        end = datetime.now(timezone.utc)
        # Enough history for Ichimoku cloud + 200-SMA regime + 60d vol.
        start = end - timedelta(days=700)
        return fetch_daily_bars(
            symbol, start, end, fetched_by=self.strategy_id,
            legacy_provider=p.get("provider", "yahoo"),
        )

    def _compute_signal(
        self,
        symbol: str,
        bar: Bar | None,
        p: dict[str, Any],
    ) -> tuple[float, float | None, dict]:
        """Returns (signal_0_or_1, realised_vol, metadata). Memoised per session.

        `bar` is unused (the signal derives entirely from the cached daily
        history via `_fetch_df`); it stays in the signature for call-site
        symmetry with `on_bar`, and `_select_entries` passes None at
        session-start to precompute every symbol's signal + conviction."""
        if symbol in self._daily_signals:
            sig, vol, meta = self._daily_signals[symbol]
            return sig, (vol if vol > 0 else None), meta

        df = self._fetch_df(symbol, p)
        if df is None or df.empty:
            self._daily_signals[symbol] = (0.0, 0.0, {})
            self._realised_vols[symbol] = None
            return 0.0, None, {}

        # Normalise column names: indicators expect lower-case high/low/close.
        cols = {c.lower(): c for c in df.columns}
        try:
            high = df[cols["high"]]
            low = df[cols["low"]]
            close = df[cols["close"]]
        except KeyError:
            self._daily_signals[symbol] = (0.0, 0.0, {})
            return 0.0, None, {}

        # Trader-faithful stateful long/flat (verbatim docs/strategy.py):
        # ENTRY close>cloud_top AND tenkan>kijun; EXIT close<cloud_bottom OR
        # tenkan<kijun; held (ffill) through the cloud in between. Uses the
        # trader's params (5/32/50/32) baked into the module.
        signal, meta = latest_signal_and_meta(
            close.to_numpy(), high.to_numpy(), low.to_numpy(),
        )

        # Regime gate -- only blocks NEW long entries.
        if p.get("use_regime_filter", True) and signal >= 1.0:
            regime_green, regime_reason = self._regime_ok(p)
            if not regime_green:
                signal = 0.0
                meta = {**meta, "regime_block": True, "regime_block_reason": regime_reason}

        # Conviction score for top-N ranking: how far the close sits above
        # the cloud top, as a fraction. Only meaningful for a long signal
        # (close > cloud_top ⇒ positive); a bigger gap = a more established
        # trend = higher conviction. Stashed in meta so _select_entries can
        # rank without recomputing.
        if signal >= 1.0:
            cloud_top = meta.get("cloud_top")
            last_close = float(close.iloc[-1])
            # The price the signal FIRED on (last complete daily close). The entry
            # happens on the NEXT session at the live price, so entry-vs-signal_close
            # measures how far the entry chased a gap (the KO case: signal $84.07 →
            # entered $89.21 = +6.1%). Consumed by the entry_max_gap_pct gate.
            meta = {**meta, "signal_close": last_close}
            if cloud_top and cloud_top > 0:
                meta = {**meta, "conviction": (last_close - float(cloud_top)) / float(cloud_top)}
            # At-entry extension for the "don't chase" gate (consumed in on_bar).
            # % above the 200-SMA + RSI(14) on the same history the signal uses.
            try:
                if len(close) >= 200:
                    sma200 = float(close.tail(200).mean())
                    if sma200 > 0:
                        meta = {**meta, "ext_pct": (last_close / sma200 - 1.0) * 100.0}
                # Deal-pinned (pending-M&A) signature for the veto gate: a big 12m
                # run now trading with COLLAPSED realized vol + pinned near its high
                # (WBD). Real trends keep moving (20d≈1y vol); deal ratchets go quiet
                # near the offer. Discriminator is the vol collapse (AMD +275% is a
                # real trend at ~1.0x vol; WBD +130% at 0.38x is a deal).
                if len(close) >= 252:
                    ret12 = last_close / float(close.iloc[-252]) - 1.0
                    dret = close.pct_change()
                    v20 = float(dret.tail(20).std()); v252 = float(dret.tail(252).std())
                    volratio = (v20 / v252) if v252 > 0 else 9.0
                    hi52 = float(close.tail(252).max())
                    offhi = (last_close / hi52 - 1.0) if hi52 > 0 else -1.0
                    if ret12 > 0.6 and volratio < 0.5 and offhi > -0.12:
                        meta = {**meta, "ma_deal_suspect": True,
                                "ma_deal_reason": (f"+{ret12*100:.0f}% 12m but 20d vol {volratio:.0%} of 1y "
                                                   f"+ {offhi*100:.0f}% off high — deal-pinned, not a trend")}
                d = close.diff()
                up = d.clip(lower=0).tail(14).mean()
                dn = (-d.clip(upper=0)).tail(14).mean()
                if dn and dn > 0:
                    meta = {**meta, "rsi": 100.0 - 100.0 / (1.0 + up / dn)}
                elif up and up > 0:
                    meta = {**meta, "rsi": 100.0}
                # Anti-whipsaw / regime metadata for the "don't-chop" gate:
                # how many times the long/flat signal FLIPPED in the last 40
                # bars. A name that keeps crossing the cloud (many flips) is
                # choppy and churns the strategy (buy-high/sell-low round-trips)
                # — the diagnosed failure that lost 40/54 names even executed
                # perfectly. Consumed by the entry_max_flips gate in on_bar.
                ps = position_series(close.to_numpy(), high.to_numpy(), low.to_numpy())
                recent = ps.tail(40)
                meta = {**meta, "recent_flips": max(int((recent != recent.shift()).sum()) - 1, 0)}
                # Fresh-signal flag: is the CURRENT long a cross on the LATEST bar
                # (a fresh DELTA), or has it been long since a PRIOR bar (a PAST
                # signal)? Consumed by the entry_fresh_only gate — trade the delta
                # (buy the cross day), never chase an accumulated long state / import
                # a backlog on a re-seed.
                long_fresh = bool(len(ps) >= 1 and ps.iloc[-1] >= 1.0 and (len(ps) < 2 or ps.iloc[-2] < 1.0))
                meta = {**meta, "long_fresh": long_fresh}
                # Entry-quality inputs (consumed by entry_quality_gate in on_bar).
                # volume_ratio is free from the df already in hand; rs_score needs a
                # per-symbol network fetch, so only pay it when the gate is enabled.
                vcol = cols.get("volume")
                if vcol is not None:
                    meta = {**meta, "volume_ratio_20d": _volume_ratio_20d(df[vcol])}
                if p.get("entry_quality_gate"):
                    try:
                        from ...sector_rs import compute_sector_rs
                        # Day-cached: RS is a daily factor, don't re-fetch every 15m.
                        _rsres = _daily_cached("sector_rs", symbol,
                                               lambda: compute_sector_rs(symbol) or {})
                        _rs = (_rsres or {}).get("rs_score")
                    except Exception:  # noqa: BLE001 — RS is best-effort, fail-open
                        _rs = None
                    meta = {**meta, "rs_score": _rs}
                # Earnings-proximity veto input (consumed by entry_earnings_gate).
                # Finnhub upcoming + yfinance history via the API → classify. Only
                # a confirmed blackout sets earnings_veto; a missing feed stays
                # UNKNOWN (no veto). Paid only when the gate is on.
                if p.get("entry_earnings_gate"):
                    try:
                        from ...earnings import fetch_upcoming_earnings, fetch_earnings_in_range
                        from ...gates.earnings_proximity import (
                            EarningsGateConfig as _EQC, classify as _ecl,
                            route as _ert, sessions_to as _est_to, sessions_since as _est_since)
                        import datetime as _ed0
                        _api = _resolve_api_base_light()
                        # Day-cached: earnings dates don't move intraday — fetching
                        # them every 15m per name is what caused the 429 storm.
                        _up = _daily_cached("earnings_upcoming", symbol,
                                            lambda: fetch_upcoming_earnings(symbol, _api))
                        _nd = _up.get("date") if _up else None
                        _hist = _daily_cached("earnings_history", symbol,
                                              lambda: fetch_earnings_in_range(symbol, lookback_days=1825)) or []
                        _t0s = _ed0.date.today().isoformat()
                        _pastd = [str(e["date"])[:10] for e in _hist
                                  if e.get("date") and str(e["date"])[:10] <= _t0s]
                        _ld = max(_pastd) if _pastd else None
                        _sto = _est_to(_nd) if _nd else None
                        _ssi = _est_since(_ld) if _ld else None
                        _ecfg = _EQC.from_env()
                        _est = _ecl(_sto, _ssi, bool(_up and _up.get("_estimated")), _ecfg, has_earnings=True)
                        _edec = _ert(_est, _ecfg, sessions_to_next=_sto, sessions_since_last=_ssi)
                        meta = {**meta, "earnings_veto": (_edec.action == "veto"),
                                "earnings_state": _est.name, "earnings_reason": _edec.reason}
                    except Exception:  # noqa: BLE001 — earnings is best-effort, fail-open
                        pass
            except Exception:  # noqa: BLE001 — gate metadata is best-effort
                pass

        # Vol for sizing.
        lookback = int(p.get("vol_lookback", 60))
        closes_for_vol = close.tail(lookback + 1).tolist()
        vol = realised_vol_from_closes(closes_for_vol)
        self._realised_vols[symbol] = vol
        # Stash for the portfolio-level vol scalar (_select_entries reconstructs
        # the weighted book return from these). Only the trailing window matters.
        self._closes_for_vol[symbol] = closes_for_vol
        self._daily_signals[symbol] = (signal, vol or 0.0, meta)
        return signal, vol, meta

    def _regime_ok(self, p: dict[str, Any]) -> tuple[bool, str]:
        """SPY > 200-SMA = GREEN, allow new longs. Missing/unreadable data
        fails OPEN (True, "data_missing"/"data_nan"/...) — don't block every
        long trade in the universe because the regime feed is broken — but
        unlike before, that condition is now DISTINGUISHABLE from a real
        bearish read and gets logged loudly (once/session) to the central
        run_log, not silently indistinguishable from "market is bearish".

        This is the exact bug behind the 2026-08-03 SPY incident: a NaN
        close (partial Yahoo fetch) made `NaN > sma` evaluate False, so
        "missing data" fell straight past every explicit missing-data check
        below (df non-empty, close column present, enough history) and read
        as a hard bearish veto for 9 days with no error anywhere."""
        regime_sym = p.get("regime_symbol", "SPY")
        df = self._fetch_df(regime_sym, p)
        if df is None or df.empty:
            self._log_regime_issue(regime_sym, "no data returned")
            return True, "data_missing"
        cols = {c.lower(): c for c in df.columns}
        if "close" not in cols:
            self._log_regime_issue(regime_sym, "no close column in cached data")
            return True, "data_no_close_column"
        close = df[cols["close"]]
        period = int(p.get("regime_sma_period", 200))
        if len(close) < period:
            self._log_regime_issue(
                regime_sym, f"only {len(close)} bars cached, need {period}+"
            )
            return True, "data_insufficient_history"
        last_close = close.iloc[-1]
        sma = close.tail(period).mean()
        if pd.isna(last_close) or pd.isna(sma):
            self._log_regime_issue(
                regime_sym,
                f"NaN in regime calc (last_close={last_close}, sma={sma}) "
                "— likely a partial-fetch bar; treating as missing, not bearish",
            )
            return True, "data_nan"
        return float(last_close) > float(sma), "ok"

    def _log_regime_issue(self, regime_sym: str, detail: str) -> None:
        """Fires once per session (not once per symbol) so a broken regime
        feed is loud in the central run_log + decision trace instead of
        silently vetoing every long, universe-wide, with nothing to grep
        for (see [[feedback_central_observability_fail_loud]])."""
        if self._regime_issue_logged:
            return
        self._regime_issue_logged = True
        _log.warning("IchimokuEquity[%s] regime data issue on %s: %s",
                      self.strategy_id, regime_sym, detail)
        self.log_decision(
            symbol=f"portfolio:regime-data-{regime_sym}",
            action="regime-data-degraded",
            reason=detail,
            regime_symbol=regime_sym,
        )
        try:
            from ...run_log import log_run
            log_run(
                self.strategy_id, "regime-data", "warn",
                symbol=regime_sym,
                error=detail,
                summary="regime filter fell back to fail-open (no block) — feed is broken, not bearish",
            )
        except Exception as exc:  # noqa: BLE001 — observability must never break trading
            _log.debug("regime-issue run_log post failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------- #
# Process-wide default registry — strategies that don't inject their own  #
# share this one so manual overrides from the trader UI propagate to all. #
# ---------------------------------------------------------------------- #

_DEFAULT_REGISTRY: OverrideRegistry | None = None


def _default_registry() -> OverrideRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = OverrideRegistry()
    return _DEFAULT_REGISTRY


__all__ = ["IchimokuEquityStrategy"]
