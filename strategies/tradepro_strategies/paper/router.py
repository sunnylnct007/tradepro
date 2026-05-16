"""OrderRouter — owns "how do approved orders become fills".

Service boundary: ONE router service per logical broker connection.
Strategies / risk never know whether the router is talking to a real
broker or a simulator — they publish `OrderApproved`, consume `Fill`.

Two impls today:

  PaperOrderRouter — fills market orders at the next bar's open
                     price with configurable slippage. No broker
                     calls. Use for replay sessions, ORB
                     development, walk-forward validation.
  StubLiveRouter   — placeholder for the real IBKR integration. Logs
                     orders, never fills. Replaces the catastrophic
                     scenario where someone wires up the live router
                     without writing the IBKR client first; better
                     to log and skip than to silently no-op.

Mapping to microservice split:
  - Today: routes via asyncio queues in-process.
  - Tomorrow: IBKRRouter runs in its own container (one per
    IBKR account / sub-account), subscribes to OrderApproved
    over Redis, publishes Fills over Redis. Routes by `strategy_id`
    to the right sub-account.

Limit / stop orders are intentionally NOT modelled here yet —
the first wave of strategies (ORB, VWAP mean-reversion) emit only
MARKET orders. Add a working-orders queue + bar-driven matching
when limit/stop strategies arrive.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .messages import (
    BarEvent,
    FillEvent,
    OrderApproved,
    ShutdownEvent,
)
from .strategy import Fill, Order, OrderSide, OrderType


class OrderRouter(ABC):
    """Common interface. Engine wires the router as a coroutine.

    `approved_queue` is the input (OrderApproved from RiskService).
    `bar_queue` is a tee of the bar feed so paper routers can fill
    market orders at the NEXT bar's open. Live routers ignore it.
    `fill_queue` is the output (FillEvent to Ledger + strategies).
    `shutdown_queue` triggers graceful exit.
    """

    name: str = "router"

    @abstractmethod
    async def run(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        ...


@dataclass
class PaperOrderRouter(OrderRouter):
    """Simulates fills against the next bar's open price.

    Why next-bar-open: a strategy emits an order at bar N's CLOSE
    (after seeing all the data for bar N). In live trading, that
    order would queue overnight / over the bar boundary and fill
    at bar N+1's OPEN. Filling at bar N's close instead would be
    look-ahead bias and inflate backtests. The router enforces this
    explicitly: an OrderApproved emitted at bar N waits for bar N+1's
    BarEvent before generating a Fill.

    Slippage: `slippage_bps` is applied AGAINST the order direction
    — buyers fill higher than the bar's open, sellers fill lower.
    Default 5 bps (0.05%) approximates a tight US-equity spread on
    liquid names; raise for less liquid names or larger orders.

    Commission: flat per-trade by default. Set `commission_per_share`
    for size-aware fees (US retail brokers usually free; IBKR pro tier
    charges per share).
    """

    slippage_bps: float = 5.0
    commission_per_trade: float = 0.0
    commission_per_share: float = 0.0
    name: str = "paper_router"
    # Pending market orders waiting for the next bar of their symbol.
    _pending_by_symbol: dict[str, list[OrderApproved]] = field(default_factory=dict)

    async def run(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        """The router runs two consumers concurrently:
          - approved_consumer: drains OrderApproved, enqueues pending fills
          - bar_consumer: drains BarEvent (the tee), fills pending orders
        Both terminate cleanly on shutdown.
        """
        tasks = [
            asyncio.create_task(self._approved_consumer(approved_queue, shutdown_queue)),
            asyncio.create_task(self._bar_consumer(bar_queue, fill_queue, shutdown_queue)),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def _approved_consumer(
        self,
        approved_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        while True:
            msg = await approved_queue.get()
            if isinstance(msg, ShutdownEvent):
                return
            assert isinstance(msg, OrderApproved)
            self._pending_by_symbol.setdefault(msg.order.symbol, []).append(msg)

    async def _bar_consumer(
        self,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        while True:
            msg = await bar_queue.get()
            if isinstance(msg, ShutdownEvent):
                # Propagate downstream so Ledger / strategies drain cleanly.
                await fill_queue.put(ShutdownEvent(reason="router shutdown"))
                return
            assert isinstance(msg, BarEvent)
            symbol = msg.bar.symbol
            pending = self._pending_by_symbol.get(symbol, [])
            if not pending:
                continue
            # Enforce the "fill at the NEXT bar's open" invariant. The
            # bus + fanout drain into asyncio queues that are effectively
            # unbounded, so the strategy / risk / router-approved chain
            # can land an approval before this consumer has even pulled
            # the bar the strategy emitted it on — that race would
            # otherwise fill at a bar BEFORE the emit bar (look-ahead).
            # An approval matches only when the current bar's timestamp
            # is strictly after the bar the order was approved on.
            still_pending: list[OrderApproved] = []
            for approval in pending:
                if msg.bar.timestamp <= approval.bar_at_approval.timestamp:
                    still_pending.append(approval)
                    continue
                fill = self._build_fill(approval, fill_bar=msg.bar)
                await fill_queue.put(FillEvent(fill=fill))
            if still_pending:
                self._pending_by_symbol[symbol] = still_pending
            else:
                self._pending_by_symbol.pop(symbol, None)

    def _build_fill(self, approval: OrderApproved, fill_bar) -> Fill:
        """Apply slippage + commission and produce the Fill.
        Only MARKET orders are currently supported."""
        order = approval.order
        if order.type != OrderType.MARKET:
            # Limit/stop orders need a working-orders queue and
            # bar-driven matching. Until that lands, treat anything
            # non-market as a no-fill (loud) so the operator notices.
            raise NotImplementedError(
                f"PaperOrderRouter only supports MARKET orders today; got "
                f"{order.type.value} for {order.symbol}. Add a working "
                f"orders queue before enabling limit/stop strategies."
            )
        slip_factor = self.slippage_bps / 10000.0
        if order.side == OrderSide.BUY:
            fill_price = fill_bar.open * (1.0 + slip_factor)
        else:
            fill_price = fill_bar.open * (1.0 - slip_factor)
        commission = (
            self.commission_per_trade
            + order.quantity * self.commission_per_share
        )
        return Fill(
            order_id=order.order_id or f"paper-{order.symbol}-{fill_bar.timestamp.isoformat()}",
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            fill_price=float(fill_price),
            fill_time=fill_bar.timestamp,
            commission=float(commission),
        )


@dataclass
class StubLiveRouter(OrderRouter):
    """Never fills. Logs every approved order so the operator can see
    what would have hit the broker. Use as the wiring placeholder
    until the real IBKRRouter ships — bad outcome if a strategy goes
    'live' against a no-op router that quietly accepts trades, so
    StubLiveRouter is deliberately loud."""

    name: str = "stub_live_router"

    async def run(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        import logging
        log = logging.getLogger("tradepro.paper.stub_live_router")
        log.warning(
            "StubLiveRouter is running — orders will be LOGGED, NOT FILLED. "
            "Wire IBKRRouter before flipping to live."
        )
        # Drain the bar queue silently so backpressure doesn't build.
        async def _bar_drain() -> None:
            while True:
                msg = await bar_queue.get()
                if isinstance(msg, ShutdownEvent):
                    return

        bar_task = asyncio.create_task(_bar_drain())
        try:
            while True:
                msg = await approved_queue.get()
                if isinstance(msg, ShutdownEvent):
                    await fill_queue.put(ShutdownEvent(reason="stub router shutdown"))
                    return
                log.info(
                    "STUB-LIVE would route: %s %s %s qty=%s tag=%s",
                    msg.order.strategy_id, msg.order.side.value,
                    msg.order.symbol, msg.order.quantity, msg.order.tag,
                )
        finally:
            if not bar_task.done():
                bar_task.cancel()


__all__ = ["OrderRouter", "PaperOrderRouter", "StubLiveRouter"]
