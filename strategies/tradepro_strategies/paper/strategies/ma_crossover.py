"""Moving-Average Crossover (intraday).

The thesis: a fast EMA crossing above a slow EMA is a (weak) trend-
following signal; crossing below ends it. Intraday version of the
classic daily SMA/EMA crossover — but reset per session so cross-day
state doesn't contaminate.

When it works:
  - Persistent intraday trends (gap-and-go days, news-driven runs)
  - When the slow window matches the trend's actual duration
When it doesn't:
  - Choppy days: continuous false crossovers, classic "death by a
    thousand whipsaws"
  - Low-volume opens: the first 5-10 bars produce noisy signals
    before either EMA has stabilised

Why include it:
  - Trend-following counterpart to VWAP-MR / BollingerBounce. On a
    trending day, this strategy wins; the mean-reverters lose. On a
    chop day, the inverse. A multi-strategy stack that includes both
    families is the entire point of running a comparator.
  - Familiar enough to non-quants that the comparator results are
    interpretable — "did the trend strategy beat the breakout strategy
    last month" is a useful question.

Mechanics:
  - Maintain `fast_window` and `slow_window` EMAs, both updated each
    bar, both reset at session_start.
  - LONG when fast crosses ABOVE slow (the "golden cross").
  - SHORT when fast crosses BELOW slow (the "death cross"; only if
    `direction in ("short","both")`).
  - Exit on the OPPOSITE crossover OR session close.
  - No fixed stop — the crossover IS the exit. (Trade duration is
    bounded by session length anyway.)

Params (default in `default_params`):
    fast_window         — bars in fast EMA (default 5)
    slow_window         — bars in slow EMA (default 20)
    risk_per_trade_usd  — dollars allocated per trade (default 100);
                          quantity = risk / bar.close at signal, floored
                          at 1.
    session_close_local — UTC HH:MM flatten (default "19:55")
    direction           — "long" / "short" / "both" (default "long")
    min_bars_before_trade — wait this many bars before allowing any
                          signal — gives EMAs time to stabilise.
                          Default = slow_window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

from ..registry import register_strategy
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy


@register_strategy("ma_crossover")
@dataclass
class MovingAverageCrossoverIntraday(Strategy):
    """One position per symbol, day-only. No stop loss; crossover is
    the exit. Position-size = risk_per_trade_usd / bar.close."""

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "fast_window": 5,
            "slow_window": 20,
            "risk_per_trade_usd": 100.0,
            "session_close_local": "19:50",
            "direction": "long",
            "min_bars_before_trade": None,
        }

    # ----- Lifecycle ----------------------------------------------------

    def on_session_start(self, session_date) -> None:
        self._state.clear()
        self.remember("fast_ema", None)
        self.remember("slow_ema", None)
        self.remember("bars_seen", 0)
        # Track prior cross state so we only act on a NEW crossover
        # (going from "fast<slow" to "fast>slow", not "fast>slow every
        # bar"). None until we have both EMAs.
        self.remember("prev_fast_above_slow", None)

    def on_bar(self, bar: Bar) -> list[Order]:
        p = self._params()
        self.remember("bars_seen", self.recall("bars_seen") + 1)
        self.remember("fast_ema", _update_ema(self.recall("fast_ema"), bar.close, p["fast_window"]))
        self.remember("slow_ema", _update_ema(self.recall("slow_ema"), bar.close, p["slow_window"]))

        if self._is_at_or_after_close(bar):
            return self._flatten_orders(bar)

        min_bars = p["min_bars_before_trade"] or p["slow_window"]
        if self.recall("bars_seen") < min_bars:
            return []

        fast = self.recall("fast_ema")
        slow = self.recall("slow_ema")
        if fast is None or slow is None:
            return []
        fast_above = fast > slow
        prev = self.recall("prev_fast_above_slow")
        self.remember("prev_fast_above_slow", fast_above)
        if prev is None:
            return []  # need a baseline to detect a CROSS

        cross_up = (not prev) and fast_above
        cross_dn = prev and (not fast_above)
        pos = self.position_for(bar.symbol)

        # Exit on opposite crossover before considering entry.
        if not pos.is_flat:
            if pos.is_long and cross_dn:
                return [self._close(bar, "MA-X long exit (death cross)")]
            if pos.is_short and cross_up:
                return [self._close(bar, "MA-X short exit (golden cross)")]
            return []

        # Bar-vs-fill race guard.
        if self.has_order_in_flight(bar.symbol):
            return []

        if cross_up and p["direction"] in ("long", "both"):
            return [self._enter(bar, OrderSide.BUY, fast, slow)]
        if cross_dn and p["direction"] in ("short", "both"):
            return [self._enter(bar, OrderSide.SELL, fast, slow)]
        return []

    def on_session_end(self, session_date) -> None:
        import logging
        log = logging.getLogger("tradepro.paper.ma_x")
        for pos in self.positions.values():
            if not pos.is_flat:
                log.warning(
                    "MA-X session_end: %s still has %d shares — "
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

    def _enter(self, bar: Bar, side: OrderSide, fast: float, slow: float) -> Order:
        p = self._params()
        qty_from_risk = max(1, int(p["risk_per_trade_usd"] / max(0.01, bar.close)))
        max_pos_value = (self.risk.max_position_value_usd
                         if self.risk and self.risk.max_position_value_usd else 1e9)
        qty_from_cap = max(1, int(max_pos_value / max(0.01, bar.close)))
        qty = min(qty_from_risk, qty_from_cap)
        tag = (
            f"MA-X {side.value.lower()} entry · close={bar.close:.2f} "
            f"fast={fast:.2f} slow={slow:.2f}"
        )
        return Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            quantity=qty,
            type=OrderType.MARKET,
            tag=tag,
        )

    def _flatten_orders(self, bar: Bar) -> list[Order]:
        out: list[Order] = []
        for pos in self.positions.values():
            if not pos.is_flat:
                out.append(self._close(bar, "MA-X EOD flatten", pos=pos))
        return out

    def _close(self, bar: Bar, reason: str, pos=None) -> Order:
        pos = pos or self.position_for(bar.symbol)
        side = OrderSide.SELL if pos.is_long else OrderSide.BUY
        return Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            quantity=abs(pos.quantity),
            type=OrderType.MARKET,
            tag=reason,
        )


def _update_ema(prev: float | None, value: float, window: int) -> float:
    """Standard exponential MA: prev × (1-α) + value × α, α = 2/(N+1).
    Seed from the first observation; converges quickly after that."""
    if prev is None:
        return value
    alpha = 2.0 / (window + 1)
    return prev * (1.0 - alpha) + value * alpha
