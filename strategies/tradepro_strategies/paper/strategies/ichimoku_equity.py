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
from ._equity_trader_signal import latest_signal_and_meta, sleeve_weight
# Coarse, auditable sector buckets for the optional concentration cap
# (max_per_sector). Static table — see _equity_sectors for the rationale.
from ._equity_sectors import sector_for as _sector_for
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy


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

        try:
            from ...cache import ensure_cached
            end = datetime.now(timezone.utc)
            # Enough history for Ichimoku cloud + 200-SMA regime + 60d vol.
            start = end - timedelta(days=700)
            return ensure_cached(
                p.get("provider", "yahoo"), symbol, start, end, interval="1d"
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("IchimokuEquity cache fetch failed for %s: %s", symbol, exc)
            return None

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
            if not self._regime_ok(p):
                signal = 0.0
                meta = {**meta, "regime_block": True}

        # Conviction score for top-N ranking: how far the close sits above
        # the cloud top, as a fraction. Only meaningful for a long signal
        # (close > cloud_top ⇒ positive); a bigger gap = a more established
        # trend = higher conviction. Stashed in meta so _select_entries can
        # rank without recomputing.
        if signal >= 1.0:
            cloud_top = meta.get("cloud_top")
            last_close = float(close.iloc[-1])
            if cloud_top and cloud_top > 0:
                meta = {**meta, "conviction": (last_close - float(cloud_top)) / float(cloud_top)}

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

    def _regime_ok(self, p: dict[str, Any]) -> bool:
        """SPY > 200-SMA = GREEN, allow new longs. Missing data = OK
        (don't block trading because the regime feed is broken)."""
        regime_sym = p.get("regime_symbol", "SPY")
        df = self._fetch_df(regime_sym, p)
        if df is None or df.empty:
            return True
        cols = {c.lower(): c for c in df.columns}
        if "close" not in cols:
            return True
        close = df[cols["close"]]
        period = int(p.get("regime_sma_period", 200))
        if len(close) < period:
            return True
        sma = close.tail(period).mean()
        return float(close.iloc[-1]) > float(sma)


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
