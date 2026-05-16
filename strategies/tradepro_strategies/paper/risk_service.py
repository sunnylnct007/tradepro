"""RiskService — owns "is this order allowed to leave the building".

Service boundary: ONE risk service per logical sub-account (today,
that's one per strategy; tomorrow, one per portfolio cluster sharing
capital + correlation caps). Strategies emit `OrderIntent`; the risk
service is the only thing that can turn that into `OrderApproved`.

Why a separate service instead of a function call from the engine:
  - Risk decisions need to land in the audit trail with their own
    timestamp and reason. A queue-published OrderApproved/Rejected
    is its own event the Ledger / dashboard can consume independently.
  - Cross-strategy halt logic (max_open_positions across the whole
    sub-account, not just one strategy) needs a place to live that
    isn't inside any one Strategy instance.
  - The microservices split is cheap: today this is an asyncio task
    reading an asyncio.Queue; tomorrow it's a container reading
    `orders.intent` from Redis Streams. Same `check_order` function,
    same RiskLimits dataclass, no business-logic rewrite.

The service holds a registry of `strategy_id → RiskLimits`. Each
intent is checked against its strategy's limits + the current state
the engine pushed in (capital, mark, positions). Rejections do NOT
short-circuit other intents — the queue keeps draining.

Continuous halt logic (`update_pnl_and_check_halt`) is driven from
the Ledger, not from this file — the Ledger sees Fills and computes
realised P&L, then calls back into the service via `apply_pnl_update`.
This file is only the pre-trade gate.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .messages import (
    OrderApproved,
    OrderIntent,
    OrderRejected,
    ShutdownEvent,
)
from .risk import RiskContext, RiskLimits, check_order, update_pnl_and_check_halt


# A snapshot the engine pushes in so the risk service can build a
# `RiskContext` without reaching back into other services. Kept as a
# plain dict so it serialises trivially when this moves to Redis.
StrategyRiskState = dict


@dataclass
class RiskService:
    """Async consumer wrapping `check_order`.

    `limits_by_strategy` is the per-strategy risk envelope; the engine
    populates it before starting the service. `state_provider` is a
    callable the engine wires up — given a strategy_id, it returns a
    fresh `RiskContext` (capital + mark + current positions). Keeping
    this as a callback rather than a snapshot avoids stale-state bugs
    when multiple intents land between bars.
    """

    name: str = "risk_service"
    limits_by_strategy: dict[str, RiskLimits] = field(default_factory=dict)
    state_provider: Callable[[str], RiskContext] | None = None

    def register(self, strategy_id: str, limits: RiskLimits) -> None:
        self.limits_by_strategy[strategy_id] = limits

    async def run(
        self,
        intent_queue: asyncio.Queue,
        approved_queue: asyncio.Queue,
        rejected_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        """Drain intents until ShutdownEvent. On shutdown, propagate
        a Shutdown on both output queues so the router + audit log
        drain cleanly."""
        while True:
            msg = await intent_queue.get()
            if isinstance(msg, ShutdownEvent):
                await approved_queue.put(ShutdownEvent(reason="risk shutdown"))
                # rejected_queue is the audit channel; signal there too
                # so a Ledger watching rejections knows the stream ended.
                await rejected_queue.put(ShutdownEvent(reason="risk shutdown"))
                return
            assert isinstance(msg, OrderIntent)
            await self._handle_intent(msg, approved_queue, rejected_queue)

    async def _handle_intent(
        self,
        intent: OrderIntent,
        approved_queue: asyncio.Queue,
        rejected_queue: asyncio.Queue,
    ) -> None:
        sid = intent.order.strategy_id
        limits = self.limits_by_strategy.get(sid)
        if limits is None:
            # Strategy was never registered — fail closed. This is
            # almost certainly a wiring bug; surface it loudly via
            # the audit channel so the operator sees it next review.
            await rejected_queue.put(
                OrderRejected(
                    order=intent.order,
                    code="unknown_strategy",
                    reason=f"no RiskLimits registered for strategy_id={sid}",
                    rejected_at=datetime.now(timezone.utc),
                )
            )
            return
        ctx = self._build_context(sid, intent)
        result = check_order(intent.order, limits, ctx)
        now = datetime.now(timezone.utc)
        if result.ok:
            await approved_queue.put(
                OrderApproved(
                    order=intent.order,
                    bar_at_approval=intent.bar_at_emit,
                    approved_at=now,
                )
            )
        else:
            await rejected_queue.put(
                OrderRejected(
                    order=intent.order,
                    code=result.code,
                    reason=result.reason,
                    rejected_at=now,
                )
            )

    def _build_context(self, strategy_id: str, intent: OrderIntent) -> RiskContext:
        """Build the RiskContext for this check. Prefer the engine-
        provided callback; fall back to a permissive context only
        when no provider was wired (unit tests). Production wiring
        MUST set `state_provider` — otherwise the sizing caps degrade
        to no-op and only the long-only / halt gates fire."""
        if self.state_provider is not None:
            return self.state_provider(strategy_id)
        # Test-only fallback: no positions, infinite capital, mark =
        # bar close. Keeps the gate functional for cases where the
        # caller hasn't wired state but wants halt/short checks to fire.
        return RiskContext(
            strategy_capital_usd=1e12,
            mark_price=intent.bar_at_emit.close,
            current_positions={},
            now=datetime.now(timezone.utc),
        )

    # --- Continuous-risk hooks (called by Ledger after a fill lands) ---

    def apply_pnl_update(
        self,
        strategy_id: str,
        realised_pnl_delta: float,
        unrealised_pnl: float,
        now: datetime,
    ) -> None:
        """Ledger calls this after every fill so the daily-loss and
        drawdown caps actually trip. Kept as a sync method because
        it just mutates the RiskLimits dataclass — no I/O."""
        limits = self.limits_by_strategy.get(strategy_id)
        if limits is None:
            return
        update_pnl_and_check_halt(limits, realised_pnl_delta, unrealised_pnl, now)


__all__ = ["RiskService"]
