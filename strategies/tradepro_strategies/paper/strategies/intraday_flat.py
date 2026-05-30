"""IntradayFlatStrategy — long-only intraday EOD-flat with a scanner-locked basket.

Goal in one sentence
====================
Pick the day's best Ichimoku longs at the open, trade them with strict
per-trade risk and an LLM news veto, and be in cash by the close every
single day — explainably, with every gate writing its reason to the
decision log so a trader can read the audit and know exactly why each
order did or didn't fire.

Trading model
=============

  pre-open                  scan candidates → pick top-N → lock basket
                            (one daily Ichimoku score per name, regime gate)

  entry window (intraday)   for each basket symbol, at most one entry:
                            regime → cap → halt → epic → LLM → sizing → fire

  position management       on every bar holding the position:
                            stop-loss · take-profit · time-stop · EOD flatten

  EOD                       flatten all positions before session close;
                            on_session_end backstop fires if any survive;
                            external reconciliation against IG closes
                            the loop (paper trades that loop too)

Why a scanner instead of a fixed symbol list
============================================

The user wants "the symbols can be derived by some strategy". The
scanner extends IchimokuEquityStrategy's daily signal into a continuous
strength score (see signal_bridge.ichimoku_strength_score) and ranks
the candidate universe at session start, then locks the top-N as the
day's basket. Locking the basket — no intraday re-ranking — keeps the
audit trail readable: at any moment a trader can say "today's thesis
was these N names, picked at 09:30, traded long, flat by close".

Why long-only
=============

Locked by the operator (see project memory). Short would double the
risk surface, require IG borrow availability for each epic, and split
the LLM gate's news interpretation between "good news supports long"
vs "good news vetoes a short" — twice the audit pages for twice the
edge of uncertain provenance. Add it later if/when the long-only
sleeve has been observed to earn its keep.

Why IG demo from day one
========================

Also locked. The plumbing for it landed in phase 0:
  - Order.broker_label + Order.instrument_id carry the routing intent.
  - IGEpicMap refuses to route an unmapped symbol (fail-loud).
  - The backend OMS dispatches broker=IG_DEMO orders through IGClient.

What this strategy DELIBERATELY DOES NOT do (caveats list)
==========================================================

1. No intraday rescan. Basket is locked at session_start; if a name
   turns into a screamer at 11am it is NOT added to today's book.
   Tomorrow's scanner will catch it.
2. No averaging in. One entry per basket name per day. Period.
3. No partial exits. Stop / target / time / EOD flatten the whole
   position at once. (Simpler audit, fewer "what executed when" puzzles.)
4. No cross-strategy gross exposure cap (framework gap — see ROADMAP).
5. EOD flatten depends on bars arriving in the flatten window. The
   on_session_end backstop and an out-of-band reconciliation against
   IG positions are the real guarantees of "flat by close".
6. LLM gate is fail-open by framework default. An LLM outage approves;
   set the gate config to fail-closed for live (not demo).
7. ATR + cloud + regime all use daily bars from the cache. If the
   cache is stale the scanner trades a stale view of the world.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from math import floor
from typing import Any, Callable, ClassVar

import pandas as pd

from ..ig_epic_map import DEFAULT_MAP_PATH, IGEpicMap, IGEpicMissingError
from ..llm_gate import GateDecision, LLMSignalGate
from ..overrides import OverrideRegistry
from ..registry import register_strategy
from ..signal_bridge import (
    ichimoku_daily_signal,
    ichimoku_strength_score,
)
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Position, Strategy


_log = logging.getLogger("tradepro.paper.intraday_flat")


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


@register_strategy("intraday_flat")
@dataclass
class IntradayFlatStrategy(Strategy):
    """Long-only intraday EOD-flat strategy.

    One position per basket symbol per day. Entries gated by regime,
    risk envelope, epic availability, and LLM news sentiment. Every
    decision writes a structured entry to the per-symbol decision log
    so a trader can scrub a single day's behaviour without reading
    the strategy source.
    """

    source: ClassVar[str] = "trader-quant"
    status: ClassVar[str] = "evaluating"
    # Enough daily history for the 200-SMA regime filter + the
    # Ichimoku cloud projection (senkou_b + displacement). Ichimoku
    # Equity uses the same horizon for the same reason.
    default_lookback_days: ClassVar[int] = 700
    caveats: ClassVar[list[str]] = [
        "Long-only; the scanner discards any symbol whose daily Ichimoku "
        "signal is non-bullish — no shorting in v1.",
        "Basket is LOCKED at session_start. A name that turns bullish "
        "mid-session is NOT added to today's book.",
        "One entry per basket symbol per day; no averaging in.",
        "EOD flatten relies on bars arriving in the flatten window. "
        "The on_session_end backstop emits a flatten unconditionally "
        "but cannot fill if the bar bus has already closed — an "
        "out-of-band IG reconciliation is the real guarantee.",
        "LLM gate fails open by framework default; for live (not demo) "
        "set LLMGateConfig.fail_open = False.",
        "Daily signal + ATR come from the on-disk cache (yfinance by "
        "default). Stale cache = stale basket.",
        "Overnight leftovers (positions seeded from the broker that "
        "the prior session's flatten missed) are flagged with "
        "`alert-overnight-leftover` and flattened on the first "
        "in-window bar. The strategy doesn't fabricate a stop/target "
        "for them since the original entry thesis is lost.",
    ]

    # ── Internal session state (NOT in default_params) ───────────────
    # Symbols picked by the scanner this session.
    _basket: list[str] = field(default_factory=list)
    # Per-symbol scanner outputs kept across the day for the audit log
    # and for sizing (atr is the stop-distance unit).
    _basket_atr: dict[str, float] = field(default_factory=dict)
    _basket_strength: dict[str, float] = field(default_factory=dict)
    _basket_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Symbols already entered this session (the "one-per-day" guard).
    _entries_today: set[str] = field(default_factory=set)
    # Symbols whose flatten order already emitted (don't double-emit).
    _flatten_emitted: set[str] = field(default_factory=set)
    # Per-position risk levels — set on the entry's fill (not on the
    # entry's emission) so they reference the actual fill price.
    _position_stop: dict[str, float] = field(default_factory=dict)
    _position_target: dict[str, float] = field(default_factory=dict)
    _position_open_at: dict[str, datetime] = field(default_factory=dict)
    _position_entry_price: dict[str, float] = field(default_factory=dict)
    # Cached helpers wired in __post_init__.
    _overrides: OverrideRegistry | None = None
    _gate: LLMSignalGate | None = None
    _epic_map: IGEpicMap | None = None
    # Regime decision computed at session_start — reused for the rest
    # of the day so the entry path doesn't re-fetch SPY on every bar.
    _regime_bull: bool = True
    _regime_detail: dict[str, Any] = field(default_factory=dict)

    # ─────────────────────────────────────────────────────────────────
    # Parameters
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            # Universe -----------------------------------------------------
            # The candidate set the scanner ranks. The scanner then
            # intersects with the IG epic map's mapped symbols so a
            # name without a populated epic can't accidentally make
            # the basket.
            "candidates": ["SPY", "QQQ", "IWM", "DIA", "XLF"],
            "top_n": 5,

            # Regime -------------------------------------------------------
            # SPY 200-SMA is the BULL/BEAR switch. BEAR → no entries
            # at all (existing positions can still hit their exits).
            "use_regime_filter": True,
            "regime_symbol": "SPY",
            "regime_sma_period": 200,

            # Per-trade risk + sizing -------------------------------------
            # We size from the stop distance, not from capital: a 1.5×ATR
            # stop on a $300 stock with $5 ATR means qty = 100 / (1.5*5)
            # ≈ 13 shares so the stop-out costs $100. ATR-anchored stops
            # adapt across symbols of very different price levels.
            "risk_per_trade_usd": 100.0,
            "stop_atr_mult": 1.5,
            "target_atr_mult": 2.5,        # 2.5/1.5 = ~1.67 R reward:risk
            "max_hold_minutes": 240,        # 4-hour time-stop

            # Session timing (UTC HH:MM strings, compared to bar.time())
            # Defaults assume US-equity DST (Mar–Nov):
            #   09:30 ET = 13:30 UTC, 16:00 ET = 20:00 UTC.
            # Outside DST shift each value by +1h (set explicitly in
            # the strategy config rather than auto-detecting — explicit
            # is debuggable). Same convention as ORB and VWAP_MR.
            "entry_window_start_utc": "13:35",   # 5min after open
            "entry_window_end_utc":   "18:00",   # no fresh entries last 2h
            "flatten_start_utc":      "19:50",   # 10min before close
            "session_close_utc":      "20:00",

            # Strategy capital ---------------------------------------------
            # Used only for risk-envelope %-of-capital caps. Live sizing
            # comes from the per-trade USD risk, not from capital splits.
            "capital_usd": 100_000.0,

            # IG routing ---------------------------------------------------
            "broker_label": "IG_DEMO",
            # None → DEFAULT_MAP_PATH (alongside ig_epic_map.py).
            "ig_epic_map_path": None,

            # Data ---------------------------------------------------------
            "provider": "yahoo",

            # Injectables (tests + the strategy runner) ------------------
            "_data_fn": None,             # def(symbol) -> pd.DataFrame | None
            "_override_registry": None,
            "_llm_gate": None,
            "_epic_map": None,            # pre-built IGEpicMap (tests)
        }

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        p = self._p()

        reg = p.get("_override_registry")
        if reg is None:
            reg = _default_registry()
        self._overrides = reg

        self._gate = p.get("_llm_gate")

        em = p.get("_epic_map")
        if em is None:
            em = self._load_epic_map(p)
        self._epic_map = em

    def on_session_start(self, session_date: datetime) -> None:  # type: ignore[override]
        """Pre-market: scanner + regime gate + basket lock.

        Idempotent — re-running it resets every per-session bucket so
        the engine's crash-recovery can call us again without dragging
        in yesterday's basket."""
        p = self._p()
        self._basket = []
        self._basket_atr.clear()
        self._basket_strength.clear()
        self._basket_meta.clear()
        self._entries_today.clear()
        self._flatten_emitted.clear()

        # Pre-load positions from params.initial_positions if the daemon
        # provided them. For an EOD-flat strategy any seeded position is
        # an OVERNIGHT LEFTOVER — the prior session's flatten did not
        # close it. The strategy didn't choose this position, so it has
        # no recorded stop/target/open_time; safest behaviour is to log
        # a clear alert + flatten on the first bar that reaches us in
        # the entry window (and certainly on every EOD bar after that).
        # See `seed_positions()` for the equivalent direct-call entry
        # point used by paper_session._seed_strategy_positions_from_broker.
        initial = p.get("initial_positions") or {}
        if isinstance(initial, dict):
            for sym, qty in initial.items():
                try:
                    qty_int = int(qty)
                except (TypeError, ValueError):
                    continue
                if qty_int != 0:
                    self._seed_overnight_leftover(sym, qty_int, session_date)

        candidates = list(p.get("candidates", []))
        if not candidates:
            self.log_decision(
                symbol="_session", action="session-skipped-empty-candidates",
                reason="no candidates in params; nothing to scan",
            )
            return

        # Filter to symbols the epic map can route. A symbol without a
        # populated epic can't reach IG — so the scanner pre-filters
        # rather than letting the entry path discover the gap and waste
        # cycles on it.
        mapped = set(self._epic_map.mapped_symbols()) if self._epic_map else set()
        routable = []
        for sym in candidates:
            if sym in mapped:
                routable.append(sym)
            else:
                self.log_decision(
                    symbol=sym, action="scanner-drop-no-epic",
                    reason=(
                        f"{sym!r} not mapped to an IG epic in "
                        f"ig_epic_map.json — discover via "
                        f"/api/admin/ig/search?term={sym} and populate"
                    ),
                )
        if not routable:
            self.log_decision(
                symbol="_session", action="session-skipped-no-routable",
                reason="every candidate is missing an IG epic",
            )
            return

        # Regime gate. Decided once per session — the same SPY close
        # used at 09:30 ET drives the gate for the rest of the day.
        self._regime_bull, self._regime_detail = self._evaluate_regime(p)
        if not self._regime_bull:
            self.log_decision(
                symbol="_session", action="regime-bear-no-trades",
                reason=(
                    f"{self._regime_detail.get('regime_symbol')} "
                    f"close {self._regime_detail.get('close'):.2f} "
                    f"<= {self._regime_detail.get('sma_period')}-SMA "
                    f"{self._regime_detail.get('sma'):.2f}"
                ),
                **self._regime_detail,
            )
            return

        # Score every routable candidate.
        scored: list[tuple[str, float, float, dict[str, Any]]] = []
        for sym in routable:
            df = self._fetch_df(sym, p)
            if df is None or df.empty:
                self.log_decision(
                    symbol=sym, action="scanner-drop-no-data",
                    reason="daily fetch returned empty / None",
                )
                continue

            cols = {c.lower(): c for c in df.columns}
            try:
                high = df[cols["high"]]
                low = df[cols["low"]]
                close = df[cols["close"]]
            except KeyError:
                self.log_decision(
                    symbol=sym, action="scanner-drop-bad-cols",
                    reason="df missing high/low/close",
                )
                continue

            signal, meta = ichimoku_daily_signal(
                high=high, low=low, close=close,
                tenkan=5, kijun=32, senkou_b=50, displacement=32,
            )
            if signal <= 0:
                self.log_decision(
                    symbol=sym, action="scanner-drop-no-signal",
                    reason=(
                        "daily Ichimoku signal flat — price not above "
                        "cloud, or tenkan/kijun not stacked, or chikou "
                        "behind. Long-only so we drop."
                    ),
                    **meta,
                )
                continue

            atr_series = self._compute_atr(df, period=14)
            if atr_series is None or atr_series.empty:
                self.log_decision(
                    symbol=sym, action="scanner-drop-no-atr",
                    reason="ATR(14) could not be computed (too short?)",
                )
                continue
            last_atr = float(atr_series.iloc[-1])
            if last_atr <= 0 or pd.isna(last_atr):
                self.log_decision(
                    symbol=sym, action="scanner-drop-bad-atr",
                    reason=f"ATR(14) = {last_atr}; non-positive / NaN",
                )
                continue

            last_close = float(close.iloc[-1])
            strength = ichimoku_strength_score(
                last_close=last_close, metadata=meta, atr=last_atr,
            )
            if strength is None or strength <= 0:
                self.log_decision(
                    symbol=sym, action="scanner-drop-no-score",
                    reason=(
                        f"strength score = {strength!r}; long-only "
                        f"scanner requires positive distance above kijun"
                    ),
                    atr=last_atr, last_close=last_close, **meta,
                )
                continue

            scored.append((sym, strength, last_atr, meta))

        if not scored:
            self.log_decision(
                symbol="_session", action="session-skipped-no-scored",
                reason="no candidate cleared signal / score gates today",
            )
            return

        # Rank descending; take top-N. Log each rejected candidate so
        # the trader can answer "why X but not Y" from the trace alone.
        scored.sort(key=lambda r: -r[1])
        top_n = int(p.get("top_n", 5))
        winners = scored[:top_n]
        losers = scored[top_n:]

        for sym, strength, atr_val, meta in winners:
            self._basket.append(sym)
            self._basket_strength[sym] = strength
            self._basket_atr[sym] = atr_val
            self._basket_meta[sym] = meta
        for sym, strength, atr_val, meta in losers:
            self.log_decision(
                symbol=sym, action="basket-rejected-rank",
                reason=f"strength {strength:.3f} below top-{top_n} cutoff",
                strength=strength, atr=atr_val,
            )

        self.log_decision(
            symbol="_session", action="basket-selected",
            reason=(
                f"top-{top_n} of {len(scored)} scored / "
                f"{len(routable)} routable candidates"
            ),
            basket=self._basket,
            strengths={s: round(self._basket_strength[s], 3) for s in self._basket},
            atrs={s: round(self._basket_atr[s], 4) for s in self._basket},
            regime=self._regime_detail,
        )

    def on_bar(self, bar: Bar) -> list[Order]:
        p = self._p()

        # ── A. Operator pause ──────────────────────────────────────
        if self._overrides is not None and self._overrides.is_paused(self.strategy_id):
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-paused",
                reason="strategy paused via overrides registry",
            )
            return []

        # ── B. Symbol filter ───────────────────────────────────────
        # Don't pollute the decision trace with one entry per
        # off-basket bar — that's pure noise. Matches the
        # ichimoku_equity off-universe pattern.
        #
        # Off-basket bars for a held position DO fall through so we
        # can flatten an overnight leftover even if that name didn't
        # make today's basket.
        if bar.symbol not in self._basket and self.position_for(bar.symbol).is_flat:
            return []

        # ── C. EOD flatten window (HIGHEST priority) ───────────────
        # Even if the position management or entry would fire below,
        # the EOD gate wins. Exits are NEVER LLM-gated; the close is
        # not a discretionary decision.
        if self._is_in_flatten_window(bar, p):
            orders = self._build_eod_flatten_orders(bar)
            if orders:
                return orders
            # No open positions to flatten in this bar — fall through
            # to log skip-after-entry-window if applicable.

        # ── D. Managed open position? ──────────────────────────────
        pos = self.position_for(bar.symbol)
        if not pos.is_flat:
            exit_order = self._manage_open_position(bar, pos, p)
            if exit_order is not None:
                return [exit_order]
            # Position holding, logged below in _manage_open_position.
            return []

        # No position — consider an entry.

        # ── E. Entry window ────────────────────────────────────────
        if not self._is_in_entry_window(bar, p):
            # Two distinct reasons: before start or after end. Both
            # log to the trace so a trader knows we saw the bar.
            window_start = p.get("entry_window_start_utc")
            window_end = p.get("entry_window_end_utc")
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-outside-entry-window",
                reason=(
                    f"bar.time={bar.timestamp.time().isoformat(timespec='minutes')} "
                    f"outside [{window_start}, {window_end}]"
                ),
            )
            return []

        # ── F. One entry per name per session ──────────────────────
        if bar.symbol in self._entries_today:
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-one-per-day",
                reason="entry already fired for this name this session",
            )
            return []

        # ── G. Order in flight (avoid emit-twice race) ─────────────
        if self.has_order_in_flight(bar.symbol):
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-in-flight",
                reason="prior entry order awaiting fill",
            )
            return []

        # ── H. Risk envelope halted? ───────────────────────────────
        # RiskService usually catches this before we even get here,
        # but the strategy logs it for the audit (the gate-failed
        # event is more informative than a silent "no order").
        if self.risk is not None and getattr(self.risk, "halted", False):
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-halted",
                reason=f"risk envelope halted: {self.risk.halt_reason!r}",
            )
            return []

        # ── I. Concurrency cap ────────────────────────────────────
        # If we're already at the open-position cap, no fresh entry.
        # RiskService also enforces this; we duplicate so the audit
        # line is more readable ("max_open_positions hit at 14:12 UTC,
        # in IWM at the time").
        max_open = int(p.get("top_n", 5))
        open_count = sum(1 for q in self.positions.values() if q.quantity != 0)
        if open_count >= max_open:
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-max-positions",
                reason=f"{open_count} open >= max_open_positions {max_open}",
            )
            return []

        # ── J. Epic lookup ────────────────────────────────────────
        # Scanner already filtered to mapped symbols, so this should
        # only ever fire if the JSON was edited mid-session. Belt +
        # braces — refuse the trade rather than route a None epic.
        try:
            epic_entry = self._epic_map.get(bar.symbol)
        except IGEpicMissingError as exc:
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-no-epic",
                reason=str(exc),
            )
            return []

        # ── K. LLM news gate (ENTRY ONLY) ─────────────────────────
        # Exits never reach this branch — they're handled in
        # _manage_open_position above. Feed the BASKET STRENGTH as
        # the signal magnitude so the gate's interpretation matches
        # the thesis (today's pick conviction), not an arbitrary
        # intraday tick.
        llm_scale = 1.0
        gate_decision: GateDecision | None = None
        strength = self._basket_strength.get(bar.symbol, 0.0)
        if self._gate is not None:
            try:
                gate_decision = self._gate.evaluate(bar.symbol, float(abs(strength)))
            except Exception as exc:  # noqa: BLE001
                # Defensive: LLMSignalGate.fail_open already covers
                # provider failures, but if THAT machinery itself
                # raises we don't want a bare exception to kill the
                # whole on_bar callback for the session. Log + treat
                # as APPROVED with neutral scale.
                _log.exception("LLM gate raised; treating as APPROVED")
                self.log_decision(
                    symbol=bar.symbol, bar_ts=bar.timestamp,
                    action="llm-gate-error-fail-open",
                    reason=f"gate raised {type(exc).__name__}: {exc}",
                )
            else:
                if gate_decision.action == GateDecision.VETOED:
                    self.log_decision(
                        symbol=bar.symbol, bar_ts=bar.timestamp,
                        action="skip-llm-vetoed",
                        reason=f"LLM veto: {gate_decision.reason}",
                        sentiment_score=gate_decision.sentiment_score,
                        headlines_checked=gate_decision.headlines_checked,
                        provider=gate_decision.provider_used,
                    )
                    return []
                llm_scale = float(gate_decision.scale_factor or 1.0)

        # ── L. Sizing ─────────────────────────────────────────────
        atr_val = self._basket_atr.get(bar.symbol)
        if atr_val is None or atr_val <= 0:
            # Should be impossible — scanner gates this. Defensive.
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-no-atr",
                reason="basket entry without an ATR (scanner bug?)",
            )
            return []

        stop_atr_mult = float(p["stop_atr_mult"])
        target_atr_mult = float(p["target_atr_mult"])
        risk_per_trade = float(p["risk_per_trade_usd"])

        stop_distance = stop_atr_mult * atr_val
        if stop_distance <= 0:
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-bad-stop",
                reason=f"stop_distance {stop_distance} non-positive",
            )
            return []

        raw_qty = floor(risk_per_trade / stop_distance)
        scaled_qty = max(0, int(raw_qty * llm_scale))
        if scaled_qty <= 0:
            self.log_decision(
                symbol=bar.symbol, bar_ts=bar.timestamp,
                action="skip-zero-qty",
                reason=(
                    f"risk_per_trade {risk_per_trade} / "
                    f"stop_distance {stop_distance:.4f} "
                    f"× llm_scale {llm_scale} -> qty {scaled_qty}"
                ),
                raw_qty=raw_qty, llm_scale=llm_scale,
            )
            return []

        # ── M. Build the order ────────────────────────────────────
        # Stop / target are RELATIVE TO bar.close (an estimate of the
        # entry price). on_fill re-anchors them to the actual fill
        # price so the position-management math is honest.
        entry_estimate = float(bar.close)
        stop_estimate = entry_estimate - stop_distance
        target_estimate = entry_estimate + target_atr_mult * atr_val

        # Order tag's LLM segment — covers all four cases explicitly so
        # a gate-raised-and-was-caught path (gate_decision=None but
        # self._gate set) doesn't crash on a None.action access.
        if self._gate is None:
            gate_tag = "llm=NONE"
        elif gate_decision is None:
            gate_tag = "llm=ERROR-FAIL-OPEN"
        elif gate_decision.sentiment_score is not None:
            gate_tag = f"llm={gate_decision.action}@{gate_decision.sentiment_score:+.2f}"
        else:
            gate_tag = f"llm={gate_decision.action}"
        tag = (
            f"intraday_flat ENTRY {bar.symbol} "
            f"strength={strength:+.2f} "
            f"regime={'BULL' if self._regime_bull else 'BEAR'} "
            f"atr={atr_val:.3f} "
            f"qty={scaled_qty} "
            f"stop~{stop_estimate:.2f} target~{target_estimate:.2f} "
            f"R:R~{target_atr_mult/stop_atr_mult:.2f} {gate_tag}"
        )

        order = Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=OrderSide.BUY,
            quantity=scaled_qty,
            type=OrderType.MARKET,
            tag=tag,
            risk_stop_price=stop_estimate,
            risk_target_price=target_estimate,
            confidence=self._confidence_from_strength(strength),
            broker_label=str(p.get("broker_label", "IG_DEMO")),
            instrument_id=epic_entry.epic,
        )

        self.log_decision(
            symbol=bar.symbol, bar_ts=bar.timestamp,
            action="fire-buy",
            reason=(
                f"top-{p['top_n']} basket entry; "
                f"sized from {risk_per_trade}$ at risk / "
                f"{stop_atr_mult}×ATR {atr_val:.3f}"
            ),
            quantity=scaled_qty,
            entry_estimate=entry_estimate,
            stop_estimate=stop_estimate,
            target_estimate=target_estimate,
            strength=strength,
            atr=atr_val,
            llm_scale=llm_scale,
            epic=epic_entry.epic,
        )
        self.mark_order_in_flight(bar.symbol)
        self._entries_today.add(bar.symbol)
        return [order]

    def seed_positions(self, positions: dict[str, int]) -> None:  # type: ignore[override]
        """Called by paper_session._seed_strategy_positions_from_broker
        with the broker's authoritative position state right after
        on_session_start.

        For an EOD-flat strategy any seeded position is an OVERNIGHT
        LEFTOVER — the prior session's flatten failed to close it.
        Mirrors `params.initial_positions` handling in on_session_start
        (both entry points are supported because paper_session uses
        seed_positions while the intraday daemon uses initial_positions).

        Symbols are bare tickers ("AAPL"); the daemon translates broker
        suffixes (AAPL_US_EQ → AAPL) before calling. Quantities are
        signed (positive long, negative short)."""
        now = datetime.now(timezone.utc)
        for sym, qty in positions.items():
            try:
                qty_int = int(qty)
            except (TypeError, ValueError):
                continue
            if qty_int == 0:
                continue
            # If the position is already known (e.g. a re-seed mid-session
            # from a reconciliation step), update without re-logging the
            # alert — we'd already have flagged it on the first seed.
            existing = self.positions.get(sym)
            if existing is not None and existing.quantity == qty_int:
                continue
            self._seed_overnight_leftover(sym, qty_int, now)

    def _seed_overnight_leftover(
        self, sym: str, qty: int, ts: datetime,
    ) -> None:
        """Record a position the strategy did not open this session.
        Sets the engine-visible Position so on_bar's manage path
        catches it, deliberately leaves stop/target/open_at unset so
        `_manage_open_position` treats it as a leftover (flatten on
        first in-window bar, no fabricated stop levels)."""
        pos = self.position_for(sym)
        pos.quantity = qty
        # avg_entry_price unknown; leave at 0 so any unrealised_pnl
        # math reads as a sentinel rather than pretending we know.
        self.log_decision(
            symbol=sym, bar_ts=ts,
            action="alert-overnight-leftover",
            reason=(
                f"seeded {qty} shares from broker — prior session's "
                f"flatten did not close this. Will flatten on the next "
                f"in-window bar (and on every EOD bar)."
            ),
            quantity=qty,
        )

    def on_fill(self, fill: Fill) -> None:  # type: ignore[override]
        """Engine has already updated `positions[symbol]`. We use the
        fill to re-anchor stop / target prices to the actual fill (so
        position management compares to a real price, not the bar.close
        we used when we emitted)."""
        self.clear_order_in_flight(fill.symbol)
        pos = self.position_for(fill.symbol)
        p = self._p()

        # Entry fill → record open state.
        if fill.side == OrderSide.BUY and pos.is_long and pos.quantity == fill.quantity:
            atr_val = self._basket_atr.get(fill.symbol, 0.0)
            self._position_open_at[fill.symbol] = fill.fill_time
            self._position_entry_price[fill.symbol] = fill.fill_price
            self._position_stop[fill.symbol] = (
                fill.fill_price - float(p["stop_atr_mult"]) * atr_val
            )
            self._position_target[fill.symbol] = (
                fill.fill_price + float(p["target_atr_mult"]) * atr_val
            )
            self.log_decision(
                symbol=fill.symbol, bar_ts=fill.fill_time,
                action="entry-filled",
                reason=(
                    f"filled {fill.quantity}@{fill.fill_price:.4f}; "
                    f"stop re-anchored to {self._position_stop[fill.symbol]:.4f}, "
                    f"target {self._position_target[fill.symbol]:.4f}"
                ),
                fill_price=fill.fill_price,
                quantity=fill.quantity,
            )
            return

        # Exit fill → clear position state.
        if pos.is_flat:
            entry_px = self._position_entry_price.pop(fill.symbol, None)
            self._position_stop.pop(fill.symbol, None)
            self._position_target.pop(fill.symbol, None)
            open_at = self._position_open_at.pop(fill.symbol, None)
            held_minutes = None
            if open_at is not None:
                held_minutes = round(
                    (fill.fill_time - open_at).total_seconds() / 60.0, 1
                )
            pnl_per_share = (
                fill.fill_price - entry_px
                if entry_px is not None
                else None
            )
            total_pnl = (
                pnl_per_share * fill.quantity
                if pnl_per_share is not None
                else None
            )
            self.log_decision(
                symbol=fill.symbol, bar_ts=fill.fill_time,
                action="exit-filled",
                reason=(
                    f"filled {fill.quantity}@{fill.fill_price:.4f}; "
                    f"held {held_minutes}min; pnl={total_pnl}"
                ),
                exit_price=fill.fill_price,
                entry_price=entry_px,
                pnl_per_share=pnl_per_share,
                total_pnl=total_pnl,
                held_minutes=held_minutes,
            )

    def on_session_end(self, session_date: datetime) -> None:  # type: ignore[override]
        """Backstop flatten + session summary. Any position still open
        here means the in-bar flatten gate didn't fire — that's a real
        problem (the bar bus closed before the flatten window?) and
        deserves a visible alert in the trace. The order we emit here
        cannot fire intraday (session is ending) but the audit line is
        what the post-session reconciliation script keys off."""
        leftovers = [
            sym for sym, pos in self.positions.items()
            if pos.quantity != 0
        ]
        if leftovers:
            self.log_decision(
                symbol="_session", bar_ts=session_date,
                action="alert-eod-leftovers",
                reason=(
                    f"on_session_end found OPEN positions: {leftovers}. "
                    f"The flatten window did not catch these — check "
                    f"that bars are arriving in [flatten_start_utc, "
                    f"session_close_utc] and reconcile against IG."
                ),
                leftover_symbols=leftovers,
            )
        # Session-level decision summary the operator UI can render
        # without scanning every per-symbol buffer.
        per_symbol = {
            sym: {
                "strength": round(self._basket_strength.get(sym, 0.0), 3),
                "atr": round(self._basket_atr.get(sym, 0.0), 4),
                "entered": sym in self._entries_today,
            }
            for sym in self._basket
        }
        self.log_decision(
            symbol="_session", bar_ts=session_date,
            action="session-summary",
            reason=(
                f"basket={len(self._basket)} entered={len(self._entries_today)} "
                f"leftovers={len(leftovers)}"
            ),
            basket=self._basket,
            per_symbol=per_symbol,
            regime=self._regime_detail,
        )

    # ─────────────────────────────────────────────────────────────────
    # Position management
    # ─────────────────────────────────────────────────────────────────

    def _manage_open_position(
        self,
        bar: Bar,
        pos: Position,
        p: dict[str, Any],
    ) -> Order | None:
        """Stop / target / time-stop check for an open position. Returns
        an exit order or None (hold + log). Never LLM-gated."""
        sym = bar.symbol
        stop_px = self._position_stop.get(sym)
        target_px = self._position_target.get(sym)
        open_at = self._position_open_at.get(sym)
        entry_px = self._position_entry_price.get(sym)

        # Overnight-leftover guard. A position with NO stop, NO target,
        # AND NO open_at recorded was not entered by this strategy
        # this session — it was seeded from the broker (a prior session's
        # flatten failed). We didn't choose this position so we have
        # no thesis to ride out: flatten it on the first bar that
        # reaches us in the entry window (or any time in the EOD
        # window, handled in _build_eod_flatten_orders).
        is_overnight_leftover = (
            stop_px is None and target_px is None and open_at is None
        )
        if is_overnight_leftover and self._is_in_entry_window(bar, p):
            return self._market_close(
                bar, pos,
                reason_tag="OVERNIGHT-LEFTOVER",
                detail=(
                    f"flattening {pos.quantity} shares seeded from broker "
                    f"(prior session's flatten did not close); "
                    f"bar.close={bar.close:.4f}"
                ),
                action_log="fire-overnight-leftover-flatten",
            )

        # If on_fill never ran (warm reload?) we still flatten on EOD
        # but skip the price-level checks rather than fabricate them.
        if stop_px is not None and bar.low <= stop_px:
            return self._market_close(
                bar, pos,
                reason_tag="STOP",
                detail=(
                    f"held={self._held_minutes(open_at, bar.timestamp)}min "
                    f"entry={entry_px} stop={stop_px:.4f} "
                    f"bar.low={bar.low:.4f}"
                ),
                action_log="fire-stop-loss",
            )

        if target_px is not None and bar.high >= target_px:
            return self._market_close(
                bar, pos,
                reason_tag="TARGET",
                detail=(
                    f"held={self._held_minutes(open_at, bar.timestamp)}min "
                    f"entry={entry_px} target={target_px:.4f} "
                    f"bar.high={bar.high:.4f}"
                ),
                action_log="fire-target",
            )

        if open_at is not None:
            held = (bar.timestamp - open_at).total_seconds() / 60.0
            max_hold = float(p.get("max_hold_minutes", 240))
            if held >= max_hold:
                return self._market_close(
                    bar, pos,
                    reason_tag="TIME",
                    detail=(
                        f"held={held:.1f}min >= max_hold {max_hold:.0f}min "
                        f"entry={entry_px} close={bar.close:.4f}"
                    ),
                    action_log="fire-time-stop",
                )

        # Holding — log with current marks so the trader sees the
        # distance to stop / target on the trace.
        self.log_decision(
            symbol=sym, bar_ts=bar.timestamp,
            action="hold",
            reason=(
                f"open {pos.quantity}@{entry_px}; "
                f"stop {stop_px} target {target_px}"
            ),
            bar_close=bar.close,
            distance_to_stop=(bar.close - stop_px) if stop_px is not None else None,
            distance_to_target=(target_px - bar.close) if target_px is not None else None,
            unrealised_pnl=pos.unrealised_pnl(bar.close),
        )
        return None

    def _market_close(
        self,
        bar: Bar,
        pos: Position,
        *,
        reason_tag: str,
        detail: str,
        action_log: str,
    ) -> Order:
        """Build the opposing market order. Long-only → SELL. Carries
        broker_label + instrument_id so the same OMS dispatch that
        handled the entry handles the exit."""
        p = self._p()
        try:
            epic_entry = self._epic_map.get(bar.symbol)
            epic = epic_entry.epic
        except IGEpicMissingError:
            # If somehow the epic disappeared between entry and exit
            # the OMS will reject — log and still emit so the exit
            # attempt is visible. (Better a logged reject than a silent
            # hold on a position that needs out.)
            epic = None

        tag = f"intraday_flat {reason_tag} {bar.symbol} {detail}"
        order = Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=OrderSide.SELL,
            quantity=abs(pos.quantity),
            type=OrderType.MARKET,
            tag=tag,
            broker_label=str(p.get("broker_label", "IG_DEMO")),
            instrument_id=epic,
        )
        self.log_decision(
            symbol=bar.symbol, bar_ts=bar.timestamp,
            action=action_log, reason=detail,
            quantity=abs(pos.quantity),
        )
        self._flatten_emitted.add(bar.symbol)
        self.mark_order_in_flight(bar.symbol)
        return order

    def _build_eod_flatten_orders(self, bar: Bar) -> list[Order]:
        """Iterate ALL open positions, not just bar.symbol — once the
        EOD window opens on any bar, any open position is over-stayed."""
        out: list[Order] = []
        for sym, pos in self.positions.items():
            if pos.quantity == 0:
                continue
            if sym in self._flatten_emitted:
                # Already emitted this session; don't stack.
                continue
            # We construct a synthetic Bar-like for the close call so
            # the symbol on the order matches the position, not whatever
            # bar happened to trigger the flatten window.
            ghost_bar = Bar(
                symbol=sym,
                timestamp=bar.timestamp,
                open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=0, timeframe_seconds=bar.timeframe_seconds,
            )
            out.append(self._market_close(
                ghost_bar, pos,
                reason_tag="EOD",
                detail=(
                    f"flatten window opened at "
                    f"{bar.timestamp.time().isoformat(timespec='minutes')} "
                    f"(triggered by {bar.symbol} bar)"
                ),
                action_log="fire-eod-flatten",
            ))
        return out

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    def _p(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}

    def _is_in_entry_window(self, bar: Bar, p: dict[str, Any]) -> bool:
        start_str = p.get("entry_window_start_utc")
        end_str = p.get("entry_window_end_utc")
        if not start_str or not end_str:
            return True
        t = bar.timestamp.time()
        return _parse_hhmm(start_str) <= t < _parse_hhmm(end_str)

    def _is_in_flatten_window(self, bar: Bar, p: dict[str, Any]) -> bool:
        start_str = p.get("flatten_start_utc")
        if not start_str:
            return False
        return bar.timestamp.time() >= _parse_hhmm(start_str)

    def _fetch_df(self, symbol: str, p: dict[str, Any]) -> pd.DataFrame | None:
        """Pluggable data lookup — tests inject `_data_fn`, production
        falls through to the on-disk cache (no live network call)."""
        fn: Callable[[str], pd.DataFrame | None] | None = p.get("_data_fn")
        if fn is not None:
            try:
                return fn(symbol)
            except Exception as exc:  # noqa: BLE001
                _log.debug("intraday_flat _data_fn failed for %s: %s", symbol, exc)
                return None
        try:
            from ...cache import ensure_cached
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=self.default_lookback_days)
            return ensure_cached(
                p.get("provider", "yahoo"), symbol, start, end, interval="1d",
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("intraday_flat cache fetch failed for %s: %s", symbol, exc)
            return None

    def _compute_atr(
        self, df: pd.DataFrame, period: int = 14
    ) -> pd.Series | None:
        from ...indicators import atr as _atr
        cols = {c.lower(): c for c in df.columns}
        try:
            return _atr(
                high=df[cols["high"]],
                low=df[cols["low"]],
                close=df[cols["close"]],
                period=period,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("ATR(14) failed: %s", exc)
            return None

    def _evaluate_regime(
        self, p: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """SPY > 200-SMA → BULL; otherwise BEAR. Missing data = BULL
        (don't block trading because the regime feed is broken — but
        the detail dict records why we couldn't compute it)."""
        if not p.get("use_regime_filter", True):
            return True, {"reason": "regime filter disabled"}

        regime_sym = p.get("regime_symbol", "SPY")
        df = self._fetch_df(regime_sym, p)
        if df is None or df.empty:
            return True, {
                "regime_symbol": regime_sym,
                "reason": "no data; defaulting to BULL",
            }
        cols = {c.lower(): c for c in df.columns}
        if "close" not in cols:
            return True, {
                "regime_symbol": regime_sym,
                "reason": "df missing close col; defaulting to BULL",
            }
        close = df[cols["close"]]
        period = int(p.get("regime_sma_period", 200))
        if len(close) < period:
            return True, {
                "regime_symbol": regime_sym,
                "reason": f"only {len(close)} bars < {period}; default BULL",
            }
        sma = float(close.tail(period).mean())
        last = float(close.iloc[-1])
        is_bull = last > sma
        return is_bull, {
            "regime_symbol": regime_sym,
            "close": last,
            "sma": sma,
            "sma_period": period,
            "is_bull": is_bull,
        }

    def _load_epic_map(self, p: dict[str, Any]) -> IGEpicMap | None:
        path = p.get("ig_epic_map_path") or DEFAULT_MAP_PATH
        try:
            return IGEpicMap.load(path)
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not load IG epic map %s: %s", path, exc)
            return None

    @staticmethod
    def _confidence_from_strength(strength: float) -> float:
        """Map a continuous strength to a [0, 1] confidence the rest
        of the system can read. Strength is roughly distance-above-kijun
        in ATRs scaled by cloud thickness — values clip to 1.0 around
        3 ATR. Pure heuristic; treat as advisory, not probabilistic."""
        if strength <= 0:
            return 0.0
        # 3.0 strength → 1.0 confidence; logistic-style soft cap.
        return min(1.0, strength / 3.0)

    @staticmethod
    def _held_minutes(
        opened_at: datetime | None, now: datetime
    ) -> float | None:
        if opened_at is None:
            return None
        return round((now - opened_at).total_seconds() / 60.0, 1)


# ---------------------------------------------------------------------------
# Process-wide default OverrideRegistry — shared across strategies that
# don't inject their own, so the trader UI's manual overrides propagate
# to all running instances. Mirrors the IchimokuEquityStrategy pattern.
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY: OverrideRegistry | None = None


def _default_registry() -> OverrideRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = OverrideRegistry()
    return _DEFAULT_REGISTRY


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


__all__ = ["IntradayFlatStrategy"]
