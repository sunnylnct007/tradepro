"""Bollinger Bounce (intraday).

The thesis: in a range-bound intraday environment, price tends to
"bounce" off the outer Bollinger bands and revert toward the moving-
average mid-line. Same family as VWAP-MR but with VOLATILITY-ADAPTIVE
bands rather than a single anchor — wider on volatile days, tighter
on quiet days, automatically.

When it works:
  - Choppy / range days where volatility is roughly stable
  - Liquid large-caps where the band touches are real liquidity
    events, not noise
When it doesn't:
  - Strong trends: price rides the upper band all day, the "bounce"
    never comes, stops fire repeatedly
  - Squeeze → expansion: bands compress, then explode; if you're
    long at the lower band when the move is down, the stop fires
    immediately

Why include alongside VWAP-MR:
  - They share the mean-reversion family but use different anchors.
    On a day where price drifts steadily away from VWAP (gap day),
    VWAP-MR shorts the rip; Bollinger may not trigger because the
    drift fits inside its expanding bands. Different trigger →
    different P&L → real diversification.

Mechanics:
  - Maintain a rolling window of `window` closes; recompute mean +
    stddev each bar.
  - LONG when bar.low touches/breaches lower band (mean - num_std × σ)
    AND bar closes back above it (the "bounce" confirmation).
  - SHORT when bar.high touches/breaches upper band AND closes back
    below it.
  - Target = middle band (the moving average).
  - Stop = a further `stop_std` × σ beyond the touched band.
  - Flatten at session close.

Params (default in `default_params`):
    window              — rolling bars for mean/std (default 20)
    num_std             — band width in stddev (default 2.0)
    stop_std            — extra band of stddev for stop (default 1.0)
    risk_per_trade_usd  — dollars risked at the stop (default 100)
    session_close_local — UTC HH:MM flatten (default "19:55")
    direction           — "long" / "short" / "both" (default "both")
    max_trades          — round-trip cap per session (default 3)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import time
from statistics import pstdev
from typing import Any

from ..registry import register_strategy
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy


@register_strategy("bollinger_bounce")
@dataclass
class BollingerBounceIntraday(Strategy):
    """One position per symbol, day-only. Sized by stop-distance × risk."""

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "window": 20,
            "num_std": 2.0,
            "stop_std": 1.0,
            "risk_per_trade_usd": 100.0,
            "session_close_local": "19:50",
            # Long-only by default — pairs cleanly with the
            # RiskLimits(allow_short=False) default. Override to "both"
            # only if your risk envelope permits shorts.
            "direction": "long",
            "max_trades": 3,
        }

    # ----- Lifecycle ----------------------------------------------------

    def on_session_start(self, session_date) -> None:
        self._state.clear()
        # Deque + max-len so old closes auto-evict — no manual trimming.
        self.remember("closes", deque(maxlen=self._params()["window"]))
        self.remember("trades_taken", 0)

    def on_bar(self, bar: Bar) -> list[Order]:
        closes: deque = self.recall("closes")
        closes.append(bar.close)

        if self._is_at_or_after_close(bar):
            return self._flatten_orders(bar)

        # Wait until the window is full so band values are statistically
        # meaningful (stddev of 3 samples is wildly noisy).
        if len(closes) < self._params()["window"]:
            return []

        mid = sum(closes) / len(closes)
        std = pstdev(closes)
        if std <= 0:
            return []
        p = self._params()
        upper = mid + p["num_std"] * std
        lower = mid - p["num_std"] * std

        pos = self.position_for(bar.symbol)
        if not pos.is_flat:
            return self._maybe_exit(bar, mid)

        # Skip if we've already emitted an order for this symbol but
        # haven't seen its fill round-trip back yet — otherwise the
        # bar-vs-fill race lets us stack duplicate entries.
        if self.has_order_in_flight(bar.symbol):
            return []

        if self.recall("trades_taken") >= p["max_trades"]:
            return []

        long_touch = bar.low <= lower and bar.close > lower and p["direction"] in ("long", "both")
        short_touch = bar.high >= upper and bar.close < upper and p["direction"] in ("short", "both")
        if not (long_touch or short_touch):
            return []
        side = OrderSide.BUY if long_touch else OrderSide.SELL
        # Stop placed an extra `stop_std` × σ beyond the touched band.
        if side == OrderSide.BUY:
            stop_price = lower - p["stop_std"] * std
        else:
            stop_price = upper + p["stop_std"] * std
        stop_dist = abs(bar.close - stop_price)
        if stop_dist <= 0:
            return []
        qty_from_risk = max(1, int(p["risk_per_trade_usd"] / stop_dist))
        # Also clamp by the risk envelope's position-value cap. The
        # RiskService check fails open on the first order (no mark yet)
        # so without this clamp the strategy can emit a multi-million-
        # share order when stop_dist happens to be tiny.
        max_pos_value = (self.risk.max_position_value_usd
                         if self.risk and self.risk.max_position_value_usd else 1e9)
        qty_from_cap = max(1, int(max_pos_value / max(0.01, bar.close)))
        qty = min(qty_from_risk, qty_from_cap)
        self.remember("stop_price", stop_price)
        self.remember("target_mid", mid)
        tag = (
            f"BollBounce {side.value.lower()} · "
            f"close={bar.close:.2f} mid={mid:.2f} band=[{lower:.2f},{upper:.2f}] "
            f"stop={stop_price:.2f}"
        )
        return [Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            quantity=qty,
            type=OrderType.MARKET,
            tag=tag,
        )]

    def on_session_end(self, session_date) -> None:
        import logging
        log = logging.getLogger("tradepro.paper.boll_bounce")
        for pos in self.positions.values():
            if not pos.is_flat:
                log.warning(
                    "BollingerBounce session_end: %s still has %d shares — "
                    "flatten-at-close may have raced the bus shutdown",
                    pos.symbol, pos.quantity,
                )

    # ----- Internals ----------------------------------------------------

    def _params(self) -> dict[str, Any]:
        return {**self.default_params(), **(self.params or {})}

    def _is_at_or_after_close(self, bar: Bar) -> bool:
        close_str = self._params()["session_close_local"]
        hh, mm = (int(x) for x in close_str.split(":"))
        return bar.timestamp.time() >= time(hh, mm)

    def _maybe_exit(self, bar: Bar, mid: float) -> list[Order]:
        pos = self.position_for(bar.symbol)
        stop = self.recall("stop_price")
        if pos.is_long:
            if bar.low <= stop:
                return [self._close(bar, "BollBounce long stop hit")]
            if bar.close >= mid:
                return [self._close(bar, "BollBounce long target (mid)")]
        else:
            if bar.high >= stop:
                return [self._close(bar, "BollBounce short stop hit")]
            if bar.close <= mid:
                return [self._close(bar, "BollBounce short target (mid)")]
        return []

    def _flatten_orders(self, bar: Bar) -> list[Order]:
        out: list[Order] = []
        for pos in self.positions.values():
            if not pos.is_flat:
                out.append(self._close(bar, "BollBounce EOD flatten", pos=pos))
        return out

    def _close(self, bar: Bar, reason: str, pos=None) -> Order:
        pos = pos or self.position_for(bar.symbol)
        side = OrderSide.SELL if pos.is_long else OrderSide.BUY
        self.remember("trades_taken", self.recall("trades_taken") + 1)
        return Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            quantity=abs(pos.quantity),
            type=OrderType.MARKET,
            tag=reason,
        )
