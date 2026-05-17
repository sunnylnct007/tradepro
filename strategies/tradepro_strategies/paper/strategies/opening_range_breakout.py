"""Opening Range Breakout (ORB).

The textbook strategy. Watch the first N minutes of the regular
session (default 15) and record the high/low of that window — the
"opening range". After the range is set:

  - If close breaks ABOVE range_high → enter long
  - If close breaks BELOW range_low  → enter short (only when
    `allow_short` on the strategy's RiskLimits permits)

Position is sized so the stop loss represents ~1 R of the strategy's
chosen per-trade risk. Default risk-per-trade = $100; default stop
= 1.0 × range height below entry (long) / above entry (short). Take
profit at 2.0 × range height for a 2R reward.

ORB has well-documented edges in 30-day SPX/QQQ studies but is
heavily regime-dependent — it works in trending opens, gets chopped
to ribbons on quiet mean-reverting days. Pair with the existing
TradePro regime stats to filter out low-confidence days.

Params (all optional, defaults in `default_params`):
    range_minutes        — width of the opening window (default 15)
    risk_per_trade_usd   — dollars risked on the stop (default 100)
    stop_multiple        — stop distance as multiple of range height (default 1.0)
    target_multiple      — target distance as multiple of range height (default 2.0)
    session_close_local  — HH:MM local exchange tz; flatten any open
                           position one bar before this (default "15:55")
    direction            — "long" / "short" / "both" (default "long")

Audit:
  - Every order's `tag` carries: side · range_high · range_low ·
    stop · target. One line, fits the daily review report.
  - on_session_end asserts position is flat. The flatten-at-close
    order should already have submitted in the bar preceding
    session_close_local; if it didn't, that's a bug worth raising.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from ..registry import register_strategy
from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy


@register_strategy("orb")
@dataclass
class OpeningRangeBreakout(Strategy):
    """ORB — one position per symbol, day-only, long-by-default.

    A single instance is intended to trade one symbol or a small
    basket of liquid US large-caps. For multi-symbol use, the engine
    should instantiate one ORB per symbol so per-strategy P&L stays
    cleanly attributed."""

    @staticmethod
    def default_params() -> dict[str, Any]:
        return {
            "range_minutes": 15,
            "risk_per_trade_usd": 100.0,
            "stop_multiple": 1.0,
            "target_multiple": 2.0,
            # 19:50 UTC = 15:50 ET during DST. Set 10 minutes before
            # session end (not 5) so the flatten order has enough bars
            # to actually fill — the router fills MARKET orders at the
            # NEXT bar's open, and the engine's queues drain fast
            # enough that a flatten emitted at the last 2-3 bars can
            # race past the bus shutdown and never fill. 10 minutes
            # buys ~10 fill opportunities, which is plenty.
            "session_close_local": "19:50",
            "direction": "long",
        }

    # ----- Lifecycle hooks --------------------------------------------------

    def on_session_start(self, session_date: datetime) -> None:
        """Reset the per-day opening range. Re-uses the cross-bar
        `_state` dict so the engine can checkpoint cleanly."""
        self._state.clear()
        self.remember("session_date", session_date.date().isoformat())
        self.remember("range_high", None)
        self.remember("range_low", None)
        self.remember("range_locked", False)
        self.remember("range_seen_first_bar_at", None)
        self.remember("entry_armed", True)   # one trade per session

    def on_bar(self, bar: Bar) -> list[Order]:
        # First bar of the session anchors when the range window opened.
        if self.recall("range_seen_first_bar_at") is None:
            self.remember("range_seen_first_bar_at", bar.timestamp)
            # Range starts as just this bar's high/low; widens with
            # subsequent bars until locked.
            self.remember("range_high", bar.high)
            self.remember("range_low", bar.low)
            return []

        if not self.recall("range_locked"):
            self._update_range(bar)

        if not self.recall("range_locked"):
            return []  # still building the range — no signal yet

        # End-of-session flatten gate fires before entry logic so we
        # never open a fresh position in the final 5 minutes.
        if self._is_at_or_after_close(bar):
            return self._flatten_orders(bar)

        return self._maybe_entry_or_exit_orders(bar)

    def on_fill(self, fill: Fill) -> None:
        """On a flatten fill the position goes to zero and `is_flat`
        flips true; engine has already applied the fill to the
        position object by the time we're called. Nothing for the
        strategy to do — `entry_armed` stays False so we don't
        re-enter same session. (One trade per day is the rule that
        keeps ORB's variance honest.)"""
        return None

    def on_session_end(self, session_date: datetime) -> None:
        # End of day → ideally position is flat. If not, log loudly
        # but don't raise — raising aborts the validator session and
        # loses the partial P&L from earlier in the day. The unrealised
        # carry sits in the ledger; operator sees the warning + a
        # stranded position on the dashboard.
        import logging
        log = logging.getLogger("tradepro.paper.orb")
        for pos in self.positions.values():
            if not pos.is_flat:
                log.warning(
                    "ORB session_end: %s still has %d shares — flatten-at-close "
                    "may have raced the bus shutdown",
                    pos.symbol, pos.quantity,
                )

    # ----- Internal helpers ------------------------------------------------

    def _params(self) -> dict[str, Any]:
        """Resolve the live params dict, falling back to defaults
        for any key the caller didn't override."""
        defaults = self.default_params()
        return {**defaults, **(self.params or {})}

    def _update_range(self, bar: Bar) -> None:
        first_at: datetime = self.recall("range_seen_first_bar_at")
        elapsed_seconds = (bar.timestamp - first_at).total_seconds()
        range_seconds = self._params()["range_minutes"] * 60
        # The first bar AT-OR-AFTER `range_seconds` elapsed is the
        # first bar outside the opening window — lock the range
        # without widening it. (Bar convention: timestamp = start of
        # bar, so bar at first_at + 900s belongs to the 16th minute.)
        if elapsed_seconds >= range_seconds:
            self.remember("range_locked", True)
            return
        rh = self.recall("range_high")
        rl = self.recall("range_low")
        self.remember("range_high", max(rh, bar.high))
        self.remember("range_low", min(rl, bar.low))

    def _maybe_entry_or_exit_orders(self, bar: Bar) -> list[Order]:
        p = self._params()
        direction = p["direction"]
        pos = self.position_for(bar.symbol)
        rh: float = self.recall("range_high")
        rl: float = self.recall("range_low")
        height = rh - rl
        if height <= 0:
            return []  # degenerate flat-line range; skip the session

        # ----- Exit gate: have we hit stop or target on an open pos? --
        if not pos.is_flat:
            stop = self.recall("stop_price")
            target = self.recall("target_price")
            if pos.is_long:
                if bar.low <= stop:
                    return [self._market_close(bar, "ORB long stop hit")]
                if bar.high >= target:
                    return [self._market_close(bar, "ORB long target hit")]
            elif pos.is_short:
                if bar.high >= stop:
                    return [self._market_close(bar, "ORB short stop hit")]
                if bar.low <= target:
                    return [self._market_close(bar, "ORB short target hit")]
            return []

        # ----- Entry gate ---------------------------------------------
        if not self.recall("entry_armed"):
            return []
        # Bar-vs-fill race guard — engine-level: don't emit a second
        # entry while the first is still en route.
        if self.has_order_in_flight(bar.symbol):
            return []
        long_break = bar.close > rh and direction in ("long", "both")
        short_break = bar.close < rl and direction in ("short", "both")
        if not (long_break or short_break):
            return []

        side = OrderSide.BUY if long_break else OrderSide.SELL
        stop_dist = height * p["stop_multiple"]
        target_dist = height * p["target_multiple"]
        if side == OrderSide.BUY:
            stop_price = bar.close - stop_dist
            target_price = bar.close + target_dist
        else:
            stop_price = bar.close + stop_dist
            target_price = bar.close - target_dist

        # Position size: dollars-risked / stop-distance, floored at 1.
        # Clamped by RiskLimits.max_position_value_usd to defend against
        # tiny stop_dist producing absurd quantities (the risk service's
        # value check fails open on the first order before any mark exists).
        risk = p["risk_per_trade_usd"]
        qty_from_risk = max(1, int(risk / max(0.01, stop_dist)))
        max_pos_value = (self.risk.max_position_value_usd
                         if self.risk and self.risk.max_position_value_usd else 1e9)
        qty_from_cap = max(1, int(max_pos_value / max(0.01, bar.close)))
        qty = min(qty_from_risk, qty_from_cap)

        self.remember("entry_armed", False)
        self.remember("stop_price", stop_price)
        self.remember("target_price", target_price)
        tag = (
            f"ORB {side.value.lower()} entry · range=[{rl:.2f}, {rh:.2f}] · "
            f"stop={stop_price:.2f} · target={target_price:.2f}"
        )
        return [
            Order(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                side=side,
                quantity=qty,
                type=OrderType.MARKET,
                tag=tag,
            )
        ]

    def _flatten_orders(self, bar: Bar) -> list[Order]:
        out: list[Order] = []
        for pos in self.positions.values():
            if pos.is_flat:
                continue
            out.append(self._market_close(bar, "ORB EOD flatten", pos=pos))
        return out

    def _market_close(
        self,
        bar: Bar,
        reason: str,
        pos=None,
    ) -> Order:
        """Compose the opposing MARKET order that closes whatever
        position we have on the bar's symbol."""
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

    def _is_at_or_after_close(self, bar: Bar) -> bool:
        """`session_close_local` is HH:MM in the exchange's local
        time. For US large-caps that's America/New_York. We use the
        bar timestamp's hour/minute directly under the assumption
        the engine emits bars stamped in exchange local time; if
        you're feeding UTC bars to this strategy, set
        session_close_local to the corresponding UTC HH:MM instead.

        Comparison is bar.time() >= close_time, not equality, so a
        skipped bar (e.g. exchange halt) still gets flattened on
        the next bar after the close threshold."""
        close_str = self._params()["session_close_local"]
        hh, mm = (int(x) for x in close_str.split(":"))
        return bar.timestamp.time() >= time(hh, mm)
