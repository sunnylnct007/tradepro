"""Swing — the mean-reversion sleeve, live on paper.

THE FIRST STRATEGY HERE TO REACH A BROKER WITH ITS EVIDENCE ALREADY IN PLACE.
Gates pre-registered (MEAN_REVERSION_GATES_V1.md), harness committed
(backtests/studies/mean_reversion_v2.py), graduation rule written before the
run (FORWARD_TEST_GATES_V1.md). 2,251 trades, 64.9% win, +0.85%/trade.

THE SIGNAL IS NOT REIMPLEMENTED HERE. It is imported from
`tradepro_strategies.signals.mean_reversion`, which the screen and the backtest
also import. Gate F1 asks whether live signals match the harness; if this file
contained its own copy of the rule, F1 would be measuring how carefully I
copied code between three files rather than whether the pipeline works. Parity
against the harness condition: 175,882 bars, zero disagreements.

WHAT IT CAN AND CANNOT DO, stated because the difference is measurable:

    backtest enters at the signal-bar CLOSE     64.9% win  +0.854%/trade
    this enters at the NEXT OPEN (achievable)   64.9% win  +0.769%/trade

You cannot place an order at a close you have not seen. So **+0.77%/trade is
the live baseline**, and the 0.085% gap is the cost of the delay, NOT
slippage. F3 measures what happens beyond it.

EXITS ARE CHECKED ON THE CLOSE, matching the backtest. A stop checked on the
close does not survive a gap — the worst historical trade is -17.7% against an
-8% stop for exactly that reason. This does not pretend otherwise, and F4
checks that reality is no worse than the model.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..strategy import Bar, Fill, Order, OrderSide, OrderType, Strategy
from ..registry import register_strategy
from ...signals.mean_reversion import (
    MAX_HOLD, MIN_BARS, STOP_PCT, entry_signal, exit_decision,
    stop_price, target_price,
)

_log = logging.getLogger("tradepro.paper.swing")


@register_strategy("mean_reversion_swing")
class MeanReversionSwingStrategy(Strategy):
    """Long-only dip buyer. One position per symbol, no pyramiding.

    Deliberately simple in execution: a market order at the open after the
    signal, then hold until the 20-day mean, the -8% stop, or ten sessions.
    The complexity that killed the Ichimoku sleeve live was in the execution
    loop — an in-memory lock voided by restarts, and quantity drift against
    the broker — so this keeps its state on disk and clamps to the broker
    rather than trusting its own count.
    """

    #: Fraction of configured capital committed to a single position.
    DEFAULT_POSITION_PCT = 0.05

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._entry_bar: dict[str, str] = {}      # symbol -> session date of fill
        self._fill_price: dict[str, float] = {}
        self._decided_today: set[str] = set()
        self._session_date: datetime | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────
    def on_session_start(self, session_date) -> None:  # type: ignore[override]
        self._session_date = session_date
        # The one-decision-per-symbol-per-session lock lives in `remember`,
        # which persists, NOT in memory. The paper daemon restarts on a */15
        # schedule; an in-memory lock is wiped several times a session, and
        # that is precisely what made the Ichimoku sleeve re-issue entries and
        # churn. Fails OPEN — a missing lock must never block trading.
        key = f"decided:{self._day(session_date)}"
        try:
            self._decided_today = set(self.recall(key, []) or [])
        except Exception:  # noqa: BLE001
            self._decided_today = set()
        p = self._p()
        for sym, qty in (p.get("initial_positions") or {}).items():
            try:
                if int(qty) > 0:
                    self._entry_bar.setdefault(sym, self._day(session_date))
            except (TypeError, ValueError):
                continue

    @staticmethod
    def _day(ts) -> str:
        return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]

    def _p(self) -> dict:
        return getattr(self, "params", {}) or {}

    # ── the decision ──────────────────────────────────────────────────────
    def on_bar(self, bar: Bar) -> list[Order]:
        sym = bar.symbol
        day = self._day(bar.timestamp)

        if self.has_order_in_flight(sym):
            return []

        closes, err = self._history(sym, bar)
        if closes is None:
            self.log_decision(symbol=sym, bar_ts=bar.timestamp, action="skip-no-history",
                              reason=err or "insufficient daily history")
            return []
        i = len(closes) - 1

        held = int(self.position_for(sym).quantity or 0)

        # ── exits first: a held position is never re-entered ──────────────
        if held > 0:
            fill = self._fill_price.get(sym) or float(
                getattr(self.position_for(sym), "avg_entry_price", 0) or 0)
            if fill <= 0:
                self.log_decision(symbol=sym, bar_ts=bar.timestamp, action="hold-no-basis",
                                  reason="held but no entry price known — cannot evaluate the "
                                         "stop or target, so holding rather than guessing")
                return []
            bars_held = self._bars_held(sym, day)
            do_exit, why = exit_decision(closes, i, fill_price=fill, bars_held=bars_held)
            if do_exit:
                self.log_decision(
                    symbol=sym, bar_ts=bar.timestamp, action=f"exit-{why}",
                    reason=(f"{why}: close {closes[i]:.2f} vs target "
                            f"{target_price(closes, i):.2f} / stop {stop_price(fill):.2f}, "
                            f"held {bars_held} of {MAX_HOLD} sessions"))
                self.mark_order_in_flight(sym)
                return [Order(strategy_id=self.strategy_id, symbol=sym,
                              side=OrderSide.SELL, quantity=held, type=OrderType.MARKET,
                              tag=f"swing exit {why} held={bars_held}")]
            self.log_decision(symbol=sym, bar_ts=bar.timestamp, action="hold",
                              reason=(f"close {closes[i]:.2f}, target {target_price(closes, i):.2f}, "
                                      f"stop {stop_price(fill):.2f}, session {bars_held}/{MAX_HOLD}"))
            return []

        # ── entries: one decision per symbol per session ──────────────────
        if sym in self._decided_today:
            return []
        if not entry_signal(closes, i):
            return []

        qty = self._size(bar.close)
        if qty < 1:
            self.log_decision(symbol=sym, bar_ts=bar.timestamp, action="skip-size",
                              reason=f"position budget buys 0 shares at {bar.close:.2f}")
            return []

        self._decided_today.add(sym)
        try:
            self.remember(f"decided:{day}", sorted(self._decided_today))
        except Exception:  # noqa: BLE001 — persistence must not block a trade
            _log.warning("could not persist the daily decision lock for %s", sym)

        self.log_decision(
            symbol=sym, bar_ts=bar.timestamp, action="entry",
            reason=(f"2.5σ below the 20-day mean while above the 200-SMA. "
                    f"close {closes[i]:.2f}, target {target_price(closes, i):.2f} "
                    f"(+{100*(target_price(closes,i)/closes[i]-1):.1f}%), "
                    f"stop {stop_price(closes[i]):.2f} (-{100*STOP_PCT:.0f}%), "
                    f"timeout {MAX_HOLD} sessions"))
        self.mark_order_in_flight(sym)
        return [Order(strategy_id=self.strategy_id, symbol=sym, side=OrderSide.BUY,
                      quantity=qty, type=OrderType.MARKET,
                      tag=f"swing entry 2.5sigma tgt={target_price(closes,i):.2f} "
                          f"stop={stop_price(closes[i]):.2f}")]

    # ── helpers ───────────────────────────────────────────────────────────
    def _history(self, sym: str, bar: Bar):
        """Settled daily closes from the canonical store. Never the live bus —
        the backtest ran on settled bars, so the live signal must too."""
        try:
            from ...ibkr_bars import fetch_daily_bars
            end = bar.timestamp
            df = fetch_daily_bars(sym, end - timedelta(days=500), end,
                                  fetched_by="paper.swing")
            if df is None or df.empty:
                return None, "bar store returned nothing"
            closes = [float(x) for x in df["close"].dropna().tolist()]
            if len(closes) < MIN_BARS:
                return None, f"{len(closes)} bars, need {MIN_BARS}"
            return closes, None
        except Exception as exc:  # noqa: BLE001
            return None, f"history fetch failed: {str(exc)[:120]}"

    def _bars_held(self, sym: str, today: str) -> int:
        start = self._entry_bar.get(sym)
        if not start:
            return 0
        try:
            d0 = datetime.fromisoformat(start).date()
            d1 = datetime.fromisoformat(today).date()
            # Business days between, which is what MAX_HOLD counts.
            days = (d1 - d0).days
            return max(0, days - 2 * (days // 7))
        except Exception:  # noqa: BLE001
            return 0

    def _size(self, price: float) -> int:
        p = self._p()
        capital = float(p.get("capital") or p.get("start_capital") or 0)
        pct = float(p.get("position_pct") or self.DEFAULT_POSITION_PCT)
        if capital <= 0 or price <= 0:
            return 0
        return int((capital * pct) // price)

    def on_fill(self, fill: Fill) -> None:
        self.clear_order_in_flight(fill.symbol)
        if getattr(fill, "side", None) == OrderSide.BUY or str(getattr(fill, "side", "")).endswith("BUY"):
            self._fill_price[fill.symbol] = float(fill.price)
            self._entry_bar[fill.symbol] = self._day(getattr(fill, "filled_at", None)
                                                     or self._session_date)
        else:
            self._fill_price.pop(fill.symbol, None)
            self._entry_bar.pop(fill.symbol, None)

    def on_session_end(self, session_date) -> None:  # type: ignore[override]
        return None
