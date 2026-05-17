"""VWAP Mean Reversion.

The thesis: intraday price tends to revert toward its volume-weighted
average price (VWAP) on liquid US-equity names. When price diverges
N% from session VWAP, the bet is that liquidity providers (the bulk
of the volume) will pull it back. Mirror image of ORB: ORB pays you
when the day is trending, VWAP-MR pays you when it's chopping.

Why it complements ORB:
  - ORB makes money on days that RUN (early breakout, follow-through)
  - VWAP-MR makes money on days that CHOP (range-bound, revert-to-mean)
  Run a comparator across both on the same date range and the day-by-
  day equity curves should be anti-correlated — that's the whole point
  of a multi-strategy stack.

Mechanics:
  - Track cumulative volume × typical-price across the session, divided
    by cumulative volume. Reset at session start.
  - SHORT when bar.close > VWAP × (1 + dev_pct): price is "stretched
    above" → fade.
  - LONG when bar.close < VWAP × (1 - dev_pct): price is "stretched
    below" → buy the dip.
  - Exit at VWAP touch (target = revert to mean), OR at hard stop
    placed `stop_pct` further beyond the entry (gives the trade room
    to wiggle without burning capital on tail moves).
  - Flatten at session_close_local.

Params (default in `default_params`):
    vwap_dev_pct        — entry trigger as fraction of VWAP (default 0.005 = 0.5%)
    stop_pct            — stop distance beyond entry, fraction (default 0.010 = 1.0%)
    risk_per_trade_usd  — dollars risked at the stop (default 100)
    session_close_local — UTC HH:MM to flatten at; default "19:55" UTC
                          ≈ 15:55 ET during DST.
    direction           — "long" / "short" / "both" (default "both")
    max_trades          — max round-trips per session (default 3 — VWAP
                          can trigger multiple times on choppy days; cap
                          to avoid churn)

Failure modes worth knowing:
  - Strong trending opens: VWAP-MR shorts the rip, gets stopped out
    repeatedly. Pair with a regime filter or accept losing on trend days.
  - Earnings / news days: price can dislocate WAY beyond stop. The
    risk envelope (max_position_value, daily-loss halt) is your only
    protection — strategy itself doesn't know about news.
  - Low-volume names: VWAP is noisier with sparse volume. Run this
    on liquid large-caps (SPY, QQQ, AAPL, MSFT) for meaningful results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

from ..registry import register_strategy
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy


@register_strategy("vwap_mean_reversion")
@dataclass
class VWAPMeanReversion(Strategy):
    """One position per symbol, day-only. Symmetric long/short by default
    (override via `direction` param). Sized by stop-distance × risk."""

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "vwap_dev_pct": 0.005,
            "stop_pct": 0.010,
            "risk_per_trade_usd": 100.0,
            "session_close_local": "19:50",
            # Long-only by default. The VWAP-MR setup is symmetric on
            # paper (short above, long below) but pairing with the
            # default RiskLimits(allow_short=False) makes the long
            # leg the only one that ever fills. Override to "both"
            # only when allow_short is enabled.
            "direction": "long",
            "max_trades": 3,
        }

    # ----- Lifecycle ----------------------------------------------------

    def on_session_start(self, session_date) -> None:
        # Cumulative numerator (Σ price × volume) and denominator (Σ volume)
        # so VWAP is one division away. Stored in _state for crash-recovery
        # checkpointing.
        self._state.clear()
        self.remember("cum_pv", 0.0)
        self.remember("cum_v", 0)
        self.remember("trades_taken", 0)

    def on_bar(self, bar: Bar) -> list[Order]:
        # Update VWAP first so the trade-decision logic sees the running
        # value INCLUSIVE of the current bar. This matches what an
        # operator running this live would see.
        typical = (bar.high + bar.low + bar.close) / 3.0
        self.remember("cum_pv", self.recall("cum_pv") + typical * bar.volume)
        self.remember("cum_v", self.recall("cum_v") + bar.volume)
        cum_v = self.recall("cum_v")
        if cum_v <= 0:
            return []
        vwap = self.recall("cum_pv") / cum_v
        self.remember("last_vwap", vwap)

        if self._is_at_or_after_close(bar):
            return self._flatten_orders(bar)

        pos = self.position_for(bar.symbol)
        if not pos.is_flat:
            return self._maybe_exit(bar, vwap)
        # Guard against the bar-vs-fill race — same reason as
        # BollingerBounce: don't stack entries while an order is en route.
        if self.has_order_in_flight(bar.symbol):
            return []
        return self._maybe_entry(bar, vwap)

    def on_session_end(self, session_date) -> None:
        import logging
        log = logging.getLogger("tradepro.paper.vwap_mr")
        for pos in self.positions.values():
            if not pos.is_flat:
                log.warning(
                    "VWAP-MR session_end: %s still has %d shares — "
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

    def _maybe_entry(self, bar: Bar, vwap: float) -> list[Order]:
        p = self._params()
        if self.recall("trades_taken") >= p["max_trades"]:
            return []
        if vwap <= 0:
            return []
        dev = (bar.close - vwap) / vwap
        long_trigger = dev <= -p["vwap_dev_pct"] and p["direction"] in ("long", "both")
        short_trigger = dev >= p["vwap_dev_pct"] and p["direction"] in ("short", "both")
        if not (long_trigger or short_trigger):
            return []
        side = OrderSide.BUY if long_trigger else OrderSide.SELL
        stop_dist = bar.close * p["stop_pct"]
        if stop_dist <= 0:
            return []
        qty_from_risk = max(1, int(p["risk_per_trade_usd"] / stop_dist))
        max_pos_value = (self.risk.max_position_value_usd
                         if self.risk and self.risk.max_position_value_usd else 1e9)
        qty_from_cap = max(1, int(max_pos_value / max(0.01, bar.close)))
        qty = min(qty_from_risk, qty_from_cap)
        # Stop placed BEYOND entry in the wrong direction; target = VWAP.
        if side == OrderSide.BUY:
            stop_price = bar.close - stop_dist
        else:
            stop_price = bar.close + stop_dist
        self.remember("entry_price", bar.close)
        self.remember("stop_price", stop_price)
        self.remember("target_vwap", vwap)
        tag = (
            f"VWAP-MR {side.value.lower()} · close={bar.close:.2f} "
            f"vwap={vwap:.2f} dev={dev:+.2%} stop={stop_price:.2f}"
        )
        return [Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            quantity=qty,
            type=OrderType.MARKET,
            tag=tag,
        )]

    def _maybe_exit(self, bar: Bar, vwap: float) -> list[Order]:
        pos = self.position_for(bar.symbol)
        stop = self.recall("stop_price")
        if pos.is_long:
            if bar.low <= stop:
                return [self._close(bar, "VWAP-MR long stop hit")]
            if bar.close >= vwap:
                return [self._close(bar, "VWAP-MR long target (VWAP touch)")]
        else:
            if bar.high >= stop:
                return [self._close(bar, "VWAP-MR short stop hit")]
            if bar.close <= vwap:
                return [self._close(bar, "VWAP-MR short target (VWAP touch)")]
        return []

    def _flatten_orders(self, bar: Bar) -> list[Order]:
        out: list[Order] = []
        for pos in self.positions.values():
            if not pos.is_flat:
                out.append(self._close(bar, "VWAP-MR EOD flatten", pos=pos))
        return out

    def _close(self, bar: Bar, reason: str, pos=None) -> Order:
        pos = pos or self.position_for(bar.symbol)
        side = OrderSide.SELL if pos.is_long else OrderSide.BUY
        # Bump trade counter so max_trades takes effect after this exit lands.
        self.remember("trades_taken", self.recall("trades_taken") + 1)
        return Order(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            side=side,
            quantity=abs(pos.quantity),
            type=OrderType.MARKET,
            tag=reason,
        )
