"""IchimokuEquityStrategy — daily Ichimoku trend-following paper strategy.

Converts the quant_engine daily sleeve signals into live paper orders
via the TradePro paper engine.

Execution model (Market-on-Open):
  Signal computed ONCE at session start using prior-day cached daily data.
  -> Entry/exit order placed at the FIRST bar of the new session (MOO).
  -> Position held until the daily signal flips.

Regime gate:
  SPY < 200-SMA -> no new longs (AMBER/BEAR mode, existing positions hold
  until their own exit signal fires).

Vol-targeted sizing:
  qty = min(max_leverage, target_vol / realised_vol_60d) x capital_per_slot / price

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

import pandas as pd

from ..llm_gate import GateDecision, LLMSignalGate
from ..overrides import OverrideRegistry
from ..registry import register_strategy
from ..signal_bridge import (
    ichimoku_daily_signal,
    realised_vol_from_closes,
    size_from_vol_target,
)
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
    _overrides: OverrideRegistry | None = None
    _gate: LLMSignalGate | None = None

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "symbols": [],
            "sleeve_size": 20,
            "capital_usd": 100_000.0,
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

    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        self._daily_signals.clear()
        self._realised_vols.clear()
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

    def seed_positions(self, positions: dict[str, int]) -> None:
        """Called by paper_session._seed_strategy_positions_from_broker
        (Phase 2 of task #28). Pre-populates internal position state
        so the strategy doesn't re-emit entries for symbols it already
        holds. Symbols are bare tickers (AAPL); the daemon translates
        broker suffixes (AAPL_US_EQ) before calling.

        Calls super() so the base class also populates self.positions
        which is what the engine's risk gate reads — otherwise the
        gate sees position=0 and rejects SELL orders on held longs
        as 'short_disallowed' (#86).
        """
        super().seed_positions(positions)
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

        # Long entry.
        if signal >= 1.0 and position == 0:
            # ── LLM signal gate ─────────────────────────────────────────
            # Runs BEFORE sizing so BOOSTED decisions can scale the qty.
            # The gate is advisory (fail_open=True default): an LLM error
            # never blocks trading. Exits are never gated — we can always
            # close a position regardless of news sentiment.
            gate_decision = (
                self._gate.evaluate(sym, signal)
                if self._gate is not None
                else GateDecision(
                    action=GateDecision.APPROVED, scale_factor=1.0,
                    reason="no gate configured",
                )
            )
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

            qty = size_from_vol_target(
                price=bar.close,
                capital=p["capital_usd"] / max(1, int(p["sleeve_size"])),
                target_vol=p["target_vol"],
                realised_vol=vol,
                max_leverage=p["max_leverage"],
            )
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
                    action="skip-zero-qty",
                    reason="vol-target sizer returned non-positive quantity",
                    signal=signal, vol=vol, price=float(bar.close),
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
        bar: Bar,
        p: dict[str, Any],
    ) -> tuple[float, float | None, dict]:
        """Returns (signal_0_or_1, realised_vol, metadata). Memoised per session."""
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

        signal, meta = ichimoku_daily_signal(
            high=high,
            low=low,
            close=close,
            tenkan=p["tenkan"],
            kijun=p["kijun"],
            senkou_b=p["senkou_b"],
            displacement=p["displacement"],
        )

        # Regime gate -- only blocks NEW long entries.
        if p.get("use_regime_filter", True) and signal >= 1.0:
            if not self._regime_ok(p):
                signal = 0.0
                meta = {**meta, "regime_block": True}

        # Vol for sizing.
        lookback = int(p.get("vol_lookback", 60))
        closes_for_vol = close.tail(lookback + 1).tolist()
        vol = realised_vol_from_closes(closes_for_vol)
        self._realised_vols[symbol] = vol
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
