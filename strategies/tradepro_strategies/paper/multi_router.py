"""MultiBrokerRouter — route the same engine to >1 broker at once.

Two operational shapes the engine needs to support, both fall out of
the same wrapper:

  SHADOW mode  — every approved order is sent to ALL wrapped routers.
                 Each broker reports its own Fill independently, tagged
                 with the broker name on the strategy_id (so the Ledger
                 keeps the books separate). Use this to reconcile T212
                 vs IBKR vs PaperOrderRouter side-by-side — same orders,
                 different fill paths, the diff IS the answer to
                 "which broker should I trust".

  DISPATCH mode — each strategy routes to ONE broker per the
                  `route_by_strategy_id` map. Use this when you want
                  ORB filling against T212 demo while a swing-trade
                  strategy fills against IBKR paper, sharing the same
                  bar bus + risk service.

Why a wrapper instead of >1 Engine: bar feed + risk service are
expensive, single sources of truth. Running two engines means two
bar buses (two yfinance fetches) and two risk services (each unaware
of the other's positions). A multi-router shares those upstream while
fanning out only at the execution boundary.

Microservices migration: when this splits to Redis Streams, the wrapper
disappears — each broker becomes a consumer-group subscriber to the
`orders.approved` stream, filtering on strategy_id (or accepting all
for shadow mode). The Engine doesn't change.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Optional

from .messages import (
    BarEvent,
    FillEvent,
    OrderApproved,
    ShutdownEvent,
)
from .router import OrderRouter


log = logging.getLogger("tradepro.paper.multi_router")


@dataclass
class MultiBrokerRouter(OrderRouter):
    """Wraps N concrete OrderRouters.

    Behavior depends on `mode`:
      - "shadow"   — every approval is forked to every wrapped router.
                     Each fill is re-emitted on the engine's fill_queue
                     with the strategy_id suffixed by `.<broker_name>`
                     so the Ledger keeps separate books per (strategy,
                     broker) pair.
      - "dispatch" — each approval routes to exactly one wrapped router
                     per `route_by_strategy_id`. Missing entries fall
                     back to `default_broker_name`; if that's None,
                     the approval is logged + dropped (loud).

    Each wrapped router gets its own private (approved, fill, bar,
    shutdown) queue set. The shared engine queues live OUTSIDE this
    object; the wrapper proxies between them.
    """

    routers: dict[str, OrderRouter] = field(default_factory=dict)
    mode: str = "dispatch"
    route_by_strategy_id: dict[str, str] = field(default_factory=dict)
    default_broker_name: Optional[str] = None
    name: str = "multi_router"

    def add(self, broker_name: str, router: OrderRouter) -> None:
        if broker_name in self.routers:
            raise ValueError(f"broker name already registered: {broker_name}")
        self.routers[broker_name] = router

    async def run(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if not self.routers:
            raise RuntimeError("MultiBrokerRouter has no wrapped routers")
        if self.mode not in {"shadow", "dispatch"}:
            raise ValueError(f"MultiBrokerRouter mode must be shadow|dispatch, got {self.mode!r}")

        # Per-wrapped-router queue sets. Each child router thinks it's
        # running solo — its approved/bar/fill/shutdown queues are
        # all its own. We forward between them and the shared engine
        # queues.
        child_approved: dict[str, asyncio.Queue] = {n: asyncio.Queue() for n in self.routers}
        child_fill: dict[str, asyncio.Queue] = {n: asyncio.Queue() for n in self.routers}
        child_bar: dict[str, asyncio.Queue] = {n: asyncio.Queue() for n in self.routers}
        child_shutdown: dict[str, asyncio.Queue] = {n: asyncio.Queue() for n in self.routers}

        tasks: list[asyncio.Task] = []

        # Launch each wrapped router with its own queue set.
        for name, router in self.routers.items():
            tasks.append(asyncio.create_task(
                router.run(
                    child_approved[name],
                    child_bar[name],
                    child_fill[name],
                    child_shutdown[name],
                ),
                name=f"child_router:{name}",
            ))

        # Bar fanout: every child router needs the bar feed too.
        tasks.append(asyncio.create_task(
            self._fanout_bars(bar_queue, child_bar),
            name="multi:bar_fanout",
        ))
        # Approved fanout: depends on mode.
        tasks.append(asyncio.create_task(
            self._fanout_approvals(approved_queue, child_approved),
            name="multi:approved_fanout",
        ))
        # Fill aggregator: forward every child fill onto the shared
        # fill_queue, optionally rewriting strategy_id for shadow mode.
        for name, q in child_fill.items():
            tasks.append(asyncio.create_task(
                self._forward_fills(name, q, fill_queue, len(self.routers)),
                name=f"multi:fill_fwd:{name}",
            ))

        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    # --- internal coordinators ---

    async def _fanout_bars(
        self, bar_queue: asyncio.Queue, child_bar: dict[str, asyncio.Queue]
    ) -> None:
        while True:
            msg = await bar_queue.get()
            for q in child_bar.values():
                await q.put(msg)
            if isinstance(msg, ShutdownEvent):
                return

    async def _fanout_approvals(
        self,
        approved_queue: asyncio.Queue,
        child_approved: dict[str, asyncio.Queue],
    ) -> None:
        while True:
            msg = await approved_queue.get()
            if isinstance(msg, ShutdownEvent):
                for q in child_approved.values():
                    await q.put(msg)
                return
            assert isinstance(msg, OrderApproved)
            targets = self._pick_targets(msg)
            for name in targets:
                await child_approved[name].put(msg)

    def _pick_targets(self, approval: OrderApproved) -> list[str]:
        if self.mode == "shadow":
            return list(self.routers.keys())
        # dispatch
        sid = approval.order.strategy_id
        name = self.route_by_strategy_id.get(sid, self.default_broker_name)
        if name is None:
            log.warning(
                "MultiBrokerRouter (dispatch) has no broker for "
                "strategy_id=%s and no default — dropping approval",
                sid,
            )
            return []
        if name not in self.routers:
            log.warning(
                "MultiBrokerRouter (dispatch) broker %r not in wrapped routers — dropping",
                name,
            )
            return []
        return [name]

    _children_done: set[str] = field(default_factory=set)

    async def _forward_fills(
        self,
        broker_name: str,
        child_fill_q: asyncio.Queue,
        out_q: asyncio.Queue,
        total_children: int,
    ) -> None:
        """Forward every fill from one child router onto the shared
        engine fill queue. Shadow mode rewrites strategy_id so the
        Ledger keeps per-broker books separate. Aggregator emits a
        single shutdown to the shared queue only after EVERY child
        has signalled exhaustion."""
        while True:
            msg = await child_fill_q.get()
            if isinstance(msg, ShutdownEvent):
                self._children_done.add(broker_name)
                if len(self._children_done) >= total_children:
                    await out_q.put(ShutdownEvent(reason="multi_router shutdown"))
                return
            assert isinstance(msg, FillEvent)
            if self.mode == "shadow":
                # Rewrite the fill's strategy_id so the Ledger keeps
                # one book per (strategy, broker) pair. The original
                # strategy still gets on_fill called via the engine,
                # but its own positions only update from the FIRST
                # fill it sees per order — shadow mode is for ledger
                # comparison, not for strategy state divergence.
                f = msg.fill
                tagged = replace(
                    f,
                    strategy_id=f"{f.strategy_id}.{broker_name}",
                )
                await out_q.put(FillEvent(fill=tagged))
            else:
                await out_q.put(msg)


__all__ = ["MultiBrokerRouter"]
