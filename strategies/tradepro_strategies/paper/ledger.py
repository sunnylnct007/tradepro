"""Ledger — per-strategy P&L attribution and the canonical record of
"what each strategy actually did today".

Service boundary: ONE ledger service per logical sub-account scope.
The Ledger is the ONLY thing that mutates `LedgerState`; everyone
else reads via the engine's snapshot API. Strategies never see the
ledger directly — they update their own `Position` objects when the
engine pushes a fill in via `on_fill`.

The reason Ledger is separate from the Strategy's in-memory positions:
  - Strategies are throwaway between sessions; ledger persists across.
  - Reconciliation needs an authoritative "as-of" record we trust
    over a possibly-buggy strategy implementation.
  - When the engine moves to microservices the Ledger keeps the
    durable state — strategies just emit orders and react to fills.

Computation model: realised P&L is computed FIFO. Each Fill either
opens / extends a position (cost basis updates) or reduces / flips
(realised P&L = (fill_price - avg_entry) × closed_qty, less
commission). Unrealised P&L is mark-price × open_qty − cost_basis;
the Ledger needs marks from the bar bus to refresh it, so it consumes
a tee of the bar feed alongside fills.

After every fill the Ledger calls back into the RiskService via the
optional `risk_service` reference, so daily-loss + drawdown caps trip
based on the authoritative P&L (not the strategy's view).

Wire format: `to_snapshot()` produces a JSON-friendly dict the engine
exposes via `/api/paper/ledger/<strategy_id>`. Same shape that'll go
into PostgreSQL when this moves out of the in-memory phase.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .messages import BarEvent, FillEvent, ShutdownEvent
from .strategy import Fill, OrderSide


@dataclass
class LedgerPosition:
    """Mirror of `Position` but lives on the Ledger side and tracks
    the cost basis the ledger trusts (vs. the strategy's view).

    Cost basis is kept as a signed-quantity-weighted average so a long
    that flips to short through zero gets a clean reset rather than
    contaminating the new short with the closed long's basis."""
    strategy_id: str
    symbol: str
    quantity: int = 0
    avg_entry_price: float = 0.0
    last_mark: float = 0.0
    last_mark_at: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    def unrealised_pnl(self) -> float:
        if self.quantity == 0:
            return 0.0
        return (self.last_mark - self.avg_entry_price) * self.quantity


@dataclass
class StrategyBook:
    """One book per strategy_id — keeps positions, realised P&L,
    commissions, fill count. Trades log is intentionally append-only;
    rotated by the engine at session_end if memory becomes a concern."""
    strategy_id: str
    realised_pnl: float = 0.0
    commission_paid: float = 0.0
    fills_count: int = 0
    positions: dict[str, LedgerPosition] = field(default_factory=dict)
    fills_log: list[Fill] = field(default_factory=list)

    def position_for(self, symbol: str) -> LedgerPosition:
        pos = self.positions.get(symbol)
        if pos is None:
            pos = LedgerPosition(strategy_id=self.strategy_id, symbol=symbol)
            self.positions[symbol] = pos
        return pos

    def unrealised_pnl_total(self) -> float:
        return sum(p.unrealised_pnl() for p in self.positions.values())

    def equity(self) -> float:
        """Realised P&L (commissions already netted in) + open MTM."""
        return self.realised_pnl + self.unrealised_pnl_total()


@dataclass
class Ledger:
    """Async service that owns `StrategyBook`s and posts every fill
    against the right one. Construct, register strategies, then
    schedule `run` on the engine's event loop."""

    name: str = "ledger"
    books: dict[str, StrategyBook] = field(default_factory=dict)
    # Optional back-reference so the Ledger can push P&L updates
    # into the RiskService for halt-cap evaluation. Kept optional
    # for tests that only care about P&L attribution.
    risk_service: "object | None" = None
    # Latest mark seen per symbol, updated EVERY bar regardless of
    # whether any book has an open position. apply_fill uses this
    # to seed last_mark when a fill creates a position lazily —
    # otherwise the ledger's bar consumer can race past the fill
    # consumer and skip the marks (no position yet), then the fill
    # arrives and locks last_mark to fill_price forever.
    latest_marks: dict[str, tuple[float, datetime]] = field(default_factory=dict)

    def register(self, strategy_id: str) -> StrategyBook:
        book = self.books.get(strategy_id)
        if book is None:
            book = StrategyBook(strategy_id=strategy_id)
            self.books[strategy_id] = book
        return book

    def seed_positions(
        self,
        strategy_id: str,
        positions: dict[str, int],
        avg_price: dict[str, float] | None = None,
    ) -> None:
        """Pre-populate the strategy's book with broker-held positions
        so the risk gate sees the same world the strategy does.

        Critical for position-aware strategies that emit SELL signals
        on long positions: without this, the engine's risk gate sees
        `current_position=0` and rejects the SELL as "would open short"
        (see project_broker_is_golden_source — the strategy's seed
        from broker MUST be mirrored to the ledger).

        avg_price is optional — when supplied, gives the ledger a
        cost basis so unrealised P&L makes sense; otherwise the
        position starts at last_mark which is fine for risk-gate
        purposes.
        """
        book = self.register(strategy_id)
        for sym, qty in positions.items():
            if qty == 0:
                continue
            pos = book.position_for(sym)
            pos.quantity = int(qty)
            if avg_price and sym in avg_price:
                pos.avg_entry_price = float(avg_price[sym])

    async def run(
        self,
        fill_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        """Two concurrent consumers — fills update positions, bars
        refresh marks. Both terminate on ShutdownEvent."""
        tasks = [
            asyncio.create_task(self._fill_consumer(fill_queue)),
            asyncio.create_task(self._bar_consumer(bar_queue)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def _fill_consumer(self, fill_queue: asyncio.Queue) -> None:
        while True:
            msg = await fill_queue.get()
            if isinstance(msg, ShutdownEvent):
                return
            assert isinstance(msg, FillEvent)
            self.apply_fill(msg.fill)

    async def _bar_consumer(self, bar_queue: asyncio.Queue) -> None:
        while True:
            msg = await bar_queue.get()
            if isinstance(msg, ShutdownEvent):
                return
            assert isinstance(msg, BarEvent)
            self.apply_mark(msg.bar.symbol, msg.bar.close, msg.bar.timestamp)

    # --- Pure-sync mutation API (also called from tests directly) ---

    def apply_fill(self, fill: Fill) -> None:
        """Update the per-strategy book + push the realised-P&L delta
        into the risk service so halt caps stay in sync. Pure synchronous
        so unit tests can drive it without an event loop."""
        book = self.register(fill.strategy_id)
        pos = book.position_for(fill.symbol)

        delta = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
        realised_delta = self._update_position_and_realise(pos, delta, fill.fill_price)

        # Commission is always a cost — applies whether the fill
        # opened, extended, reduced, or closed a position.
        book.realised_pnl += realised_delta - fill.commission
        book.commission_paid += fill.commission
        book.fills_count += 1
        book.fills_log.append(fill)

        # Refresh mark. Prefer the latest bar mark we've seen for the
        # symbol — fanout races mean we may have already drained ALL
        # bars from this session before the fill arrives, and using
        # fill_price would freeze unrealised P&L at the entry price
        # forever. Fall back to fill_price only if we've never seen a
        # bar for this symbol (synthetic test scenarios).
        latest = self.latest_marks.get(fill.symbol)
        if latest is not None and latest[1] >= fill.fill_time:
            pos.last_mark = latest[0]
            pos.last_mark_at = latest[1]
        else:
            pos.last_mark = fill.fill_price
            pos.last_mark_at = fill.fill_time

        # Notify the RiskService so daily-loss + drawdown halts fire.
        if self.risk_service is not None:
            self.risk_service.apply_pnl_update(
                fill.strategy_id,
                realised_delta - fill.commission,
                book.unrealised_pnl_total(),
                fill.fill_time,
            )

    def apply_mark(self, symbol: str, price: float, when: datetime) -> None:
        """Refresh `last_mark` on every open position + update the
        per-symbol latest-mark map. The map is updated unconditionally
        so a fill arriving after marks have already streamed past can
        still see the freshest price (see apply_fill)."""
        self.latest_marks[symbol] = (float(price), when)
        for book in self.books.values():
            pos = book.positions.get(symbol)
            if pos is None or pos.quantity == 0:
                continue
            pos.last_mark = float(price)
            pos.last_mark_at = when

    # --- Snapshot for the API / UI / persistence layer ---

    def to_snapshot(self, *, include_fills: int = 0) -> dict:
        """JSON-serialisable summary. Engine publishes this so the UI
        can render per-strategy scoreboards without reaching into Python
        internals.

        `include_fills` — if > 0, also include up to that many MOST
        RECENT fills per strategy. The Live-orders dashboard uses this
        to render "what trades just happened"; backtest comparator
        callers leave it at 0 because they care about aggregates."""
        return {
            "as_of_utc": datetime.now(timezone.utc).isoformat(),
            "strategies": [
                {
                    "strategy_id": book.strategy_id,
                    "realised_pnl": book.realised_pnl,
                    "unrealised_pnl": book.unrealised_pnl_total(),
                    "equity": book.equity(),
                    "commission_paid": book.commission_paid,
                    "fills_count": book.fills_count,
                    "positions": [
                        {
                            "symbol": p.symbol,
                            "quantity": p.quantity,
                            "avg_entry_price": p.avg_entry_price,
                            "last_mark": p.last_mark,
                            "unrealised_pnl": p.unrealised_pnl(),
                        }
                        for p in book.positions.values()
                        if p.quantity != 0
                    ],
                    "recent_fills": [
                        {
                            "order_id": f.order_id,
                            "symbol": f.symbol,
                            "side": f.side.value,
                            "quantity": f.quantity,
                            "fill_price": f.fill_price,
                            "fill_time": f.fill_time.isoformat(),
                            "commission": f.commission,
                        }
                        for f in (book.fills_log[-include_fills:] if include_fills else [])
                    ],
                }
                for book in self.books.values()
            ],
        }

    # --- Internal: position + realised-P&L math ---

    @staticmethod
    def _update_position_and_realise(
        pos: LedgerPosition, delta: int, price: float
    ) -> float:
        """Apply a signed quantity delta + return the realised P&L.

        Three cases, picked by whether the delta is in the SAME
        direction as the current position, opposite-but-not-flipping,
        or opposite-and-flipping-through-zero:

          1. opens / extends   → no realised P&L; recompute avg basis
          2. partial reduce    → realise on the closed portion only
          3. flip through zero → realise the full close, then open
                                 the residual at the fill price
        """
        cur_qty = pos.quantity
        if cur_qty == 0:
            pos.quantity = delta
            pos.avg_entry_price = price
            return 0.0

        same_direction = (cur_qty > 0 and delta > 0) or (cur_qty < 0 and delta < 0)
        if same_direction:
            # Weighted-average up.
            new_qty = cur_qty + delta
            pos.avg_entry_price = (
                pos.avg_entry_price * abs(cur_qty) + price * abs(delta)
            ) / abs(new_qty)
            pos.quantity = new_qty
            return 0.0

        # Opposite direction: either partial-close or flip-through-zero.
        closing_qty = min(abs(cur_qty), abs(delta))
        # For a long being SOLD: P&L = (sell_price - entry) × closed_qty
        # For a short being BOUGHT: P&L = (entry - buy_price) × closed_qty
        if cur_qty > 0:
            realised = (price - pos.avg_entry_price) * closing_qty
        else:
            realised = (pos.avg_entry_price - price) * closing_qty

        if abs(delta) <= abs(cur_qty):
            # Partial or exact close — basis unchanged, quantity moves
            # toward zero.
            pos.quantity = cur_qty + delta
            if pos.quantity == 0:
                pos.avg_entry_price = 0.0
            return realised

        # Flip through zero: residual delta opens a new position at price.
        residual = delta + cur_qty  # carries the opposite sign of cur_qty
        pos.quantity = residual
        pos.avg_entry_price = price
        return realised


__all__ = ["Ledger", "LedgerPosition", "StrategyBook"]
