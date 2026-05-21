"""Strategy base class + the wire-level dataclasses an intraday
event-driven engine passes between its components.

Design notes — the WHY behind shape choices:

- Bars arrive one at a time via `on_bar(bar)`. Strategies return a
  list of orders to place. The engine owns timing — strategies never
  block waiting for a fill, never read a clock. This makes them
  trivially replayable in a backtest: feed the same bar sequence,
  get the same orders.

- `Position` is signed (positive = long, negative = short, zero =
  flat). Avoids a `side` enum on Position and the corresponding
  `if pos.side == LONG and pos.qty > 0` boilerplate at every call
  site. Engine reconciles vs. broker after each fill.

- A strategy never modifies its own `position` directly. The engine
  applies fills and pushes the updated state in via the public
  attributes before calling `on_bar` next. Strategies that need to
  remember between bars use `self._state` or instance variables.

- `Order.tag` carries a short string like "ORB long, range_high=148.2,
  stop=147.4" — this is the audit trail. Every order placed in
  production must have a tag, and the tag should fit on one line of
  a daily review report.

- We deliberately do NOT model `cancel_order` / `modify_order` here.
  The first version places NEW orders only; positions are flattened
  by submitting opposing orders. Add cancel/modify when the first
  strategy genuinely needs them, not before.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass(frozen=True)
class Bar:
    """One time-window of OHLCV for one symbol. Frozen because bars
    are write-once-from-the-bus — letting a strategy mutate them
    would create undebuggable replay drift.

    timestamp convention: START of the bar's window. A bar at
    09:30:00 covers 09:30:00–09:30:59 (for a 60s timeframe). This
    matches Polygon and IBKR's defaults; if you switch to an
    end-of-bar feed, normalise before pushing into the engine."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    timeframe_seconds: int   # 60 for 1m bars, 300 for 5m, etc.


@dataclass
class Order:
    """An intent to trade. The engine assigns `order_id` when it
    accepts the order. `tag` is the per-order audit string the
    strategy writes — required so every fill traces back to a reason.

    Stop / limit prices are only consulted when `type` requires
    them; we don't enforce that here (engine does).

    The trailing `risk_*` + `confidence` fields are advisory metadata
    the strategy declares for downstream consumers (Task #69 intraday
    gate, hit-rate logger, audit). They never affect routing on their
    own — the pre-trade gate is the one that reads them."""
    strategy_id: str
    symbol: str
    side: OrderSide
    quantity: int            # shares — fractional not supported for v1
    type: OrderType
    tag: str                 # one-line audit: "ORB long, range=148.2/146.8"
    limit_price: float | None = None
    stop_price: float | None = None
    # Filled in by the engine — not by strategies
    order_id: str | None = None
    submitted_at: datetime | None = None
    # Optional good-til; None = day order, expires at session close
    good_til: datetime | None = None
    # Advisory metadata for the pre-trade gate / audit (see Task #69
    # step E). `risk_stop_price` and `risk_target_price` are the
    # strategy's intended stop-loss and take-profit reference levels
    # (distinct from the stop_price field, which means "this is a
    # stop-order TYPE"). `confidence` is the strategy's self-rated
    # probability of the entry working out, in [0, 1].
    risk_stop_price: float | None = None
    risk_target_price: float | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class Fill:
    """An accepted fill from the broker. Frozen for the same reason
    Bar is: replay determinism."""
    order_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    quantity: int
    fill_price: float
    fill_time: datetime
    commission: float        # paid in USD; engine converts FX before reconciliation


@dataclass
class Position:
    """Per-strategy, per-symbol holding. quantity is SIGNED — positive
    long, negative short, zero flat. avg_entry_price is volume-
    weighted across all fills that built the current position; resets
    when the position flips through zero.

    `unrealised_pnl(mark)` lets a strategy decide "are we still in
    profit?" without the engine needing to push live mark prices on
    every bar."""
    strategy_id: str
    symbol: str
    quantity: int = 0
    avg_entry_price: float = 0.0
    opened_at: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    def unrealised_pnl(self, mark_price: float) -> float:
        if self.quantity == 0:
            return 0.0
        return (mark_price - self.avg_entry_price) * self.quantity


@dataclass
class Strategy(ABC):
    """Event-driven intraday strategy base class.

    Subclass and implement `on_bar`. The engine drives the lifecycle:

        engine                    strategy
        ------                    --------
        market open               on_session_start(date)
        bar arrives  ─────────►   on_bar(bar)         → returns [Order, ...]
        broker fills order        on_fill(fill)
        bar arrives  ─────────►   on_bar(bar)         → returns [Order, ...]
        ...
        market close              on_session_end(date)

    Lifecycle methods are no-ops by default — most strategies only
    need on_bar. Override the others only when you need their hook.

    A strategy holds at most one position per symbol it trades.
    Strategies that want multi-symbol exposure (e.g. pairs, sector
    baskets) hold one Position per symbol in `self.positions` and
    iterate."""

    strategy_id: str             # unique per instance, used for sub-account routing
    params: dict[str, Any] = field(default_factory=dict)
    risk: "RiskLimits | None" = None
    positions: dict[str, Position] = field(default_factory=dict)
    _state: dict[str, Any] = field(default_factory=dict)
    # Symbols with an order emitted but no fill seen yet. Engine maintains
    # this around `emit → on_fill`; strategies query via has_order_in_flight().
    _in_flight_symbols: set[str] = field(default_factory=set)

    # --- Lifecycle hooks the engine calls --------------------------

    def on_session_start(self, session_date: datetime) -> None:
        """Called once before the first bar of the day. Reset any
        per-session state (opening range, daily P&L, etc.) here."""
        return None

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[Order]:
        """Core hook. Receive one bar, return zero-or-more orders.

        MUST be deterministic given the same bar sequence + same
        starting state — otherwise backtest-vs-live reconciliation
        fails. No clock reads, no random numbers (seed if you need
        randomness), no network calls."""
        ...

    def on_fill(self, fill: Fill) -> None:
        """Called by the engine after a fill is applied to the
        strategy's position. Default impl is no-op; override when
        you want to log entries / update internal state on fills."""
        return None

    def on_session_end(self, session_date: datetime) -> None:
        """Called once after the last bar of the day. Most intraday
        strategies use this to assert positions are flat (i.e. the
        flatten-at-close order already filled)."""
        return None

    # --- Helpers strategies call inside on_bar ----------------------

    def has_order_in_flight(self, symbol: str) -> bool:
        """True if an order this strategy emitted has NOT yet seen its
        fill applied via on_fill. Use this to guard against the classic
        "emit on bar N, see bar N+1 before bar N's fill lands, emit
        again" race that fills the same intended position N times.

        The engine queues bar fanout independently of fill dispatch,
        so a strategy can observe stale `position_for(symbol).is_flat`
        right after emitting an entry — by checking
        `has_order_in_flight(symbol)` first, you avoid stacking
        duplicate entries while you wait for the fill to round-trip.
        """
        return symbol in self._in_flight_symbols

    def mark_order_in_flight(self, symbol: str) -> None:
        """Call right after emitting an order. The engine calls
        `clear_order_in_flight(symbol)` when on_fill fires for the
        same symbol. Wrapping `emit` in a helper makes this less
        error-prone; today it's manual at the call site."""
        self._in_flight_symbols.add(symbol)

    def clear_order_in_flight(self, symbol: str) -> None:
        self._in_flight_symbols.discard(symbol)

    def position_for(self, symbol: str) -> Position:
        """Get or lazy-create the Position for a symbol. Strategies
        always read through this — never `self.positions[symbol]`
        directly — so the dict autopopulates on first use."""
        pos = self.positions.get(symbol)
        if pos is None:
            pos = Position(strategy_id=self.strategy_id, symbol=symbol)
            self.positions[symbol] = pos
        return pos

    def remember(self, key: str, value: Any) -> None:
        """Store cross-bar state. Lives in `_state` so the engine
        can snapshot it for crash recovery without instrumenting
        every concrete strategy."""
        self._state[key] = value

    def recall(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)
