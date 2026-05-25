"""Engine — wires the paper-trading services together for a session.

Service topology this engine assembles (every arrow is an asyncio
queue today, a Redis Stream tomorrow):

    BarBus ─bars─┬─► strategy.on_bar() ─intents─► RiskService ─approved─► Router ─fills─┬─► Ledger
                 │                                                                       │
                 └──────────────────────────────────────────────────────────► Router ────┘
                 └──────────────────────────────────────────────────────────► Ledger (mark refresh)

Why this file exists separately from the services:
  - Services are PURE consumers/producers — they only know their
    queues. The engine is the only place that knows how the queues
    connect, which makes "swap PaperOrderRouter for IBKRRouter"
    a one-line change in this file with zero diff anywhere else.
  - The fanout from "BarBus output" to "every strategy + router +
    ledger" needs a single coordinator. That coordinator is
    `_bar_fanout`. Keeping it here avoids pushing fanout logic into
    the BarBus itself (which would couple the bus to its consumers).

Strategy fanout model: every Strategy registered with the engine
receives EVERY bar for the symbol(s) it subscribed to. Today the
fanout is in-process via dedicated `asyncio.Queue` per strategy. The
microservices version maps to Redis Streams consumer groups: one
group per strategy_id, the bus publishes once.

Session lifecycle (per `Engine.run()`):
  1. on_session_start(date) for every registered strategy
  2. spin up tasks: bus, fanout, per-strategy consumers, risk, router,
     ledger
  3. await bus exhaustion (ShutdownEvent propagates through the chain)
  4. on_session_end(date) for every registered strategy
  5. return the Ledger snapshot
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .bar_bus import BarBus
from .ledger import Ledger
from .messages import (
    BarEvent,
    FillEvent,
    OrderIntent,
    OrderRejected,
    ShutdownEvent,
)
from .risk import RiskContext, RiskLimits
from .risk_service import RiskService
from .router import OrderRouter
from .strategy import Bar, Strategy

log = logging.getLogger(__name__)


@dataclass
class StrategyRegistration:
    """One row in the engine's strategy registry. `symbols` is the
    set of symbols the strategy wants to see bars for; `capital_usd`
    is the sub-account allocation used by risk's % checks."""
    strategy: Strategy
    symbols: set[str]
    capital_usd: float = 100_000.0


@dataclass
class Engine:
    """Per-session orchestrator. Construct once, register strategies,
    call `run(session_date)`.

    `bus` and `router` are pluggable so the same engine drives:
      - replay sessions (ReplayBarBus / YfinanceIntradayBus + PaperOrderRouter)
      - live paper sessions on the Mac (LiveIBKRBarBus + PaperOrderRouter)
      - live trading (LiveIBKRBarBus + IBKRRouter)
    The engine itself is identical across all three.
    """

    bus: BarBus
    router: OrderRouter
    risk: RiskService = field(default_factory=RiskService)
    ledger: Ledger = field(default_factory=Ledger)
    registrations: dict[str, StrategyRegistration] = field(default_factory=dict)
    # Bounded queues force back-pressure: the bus can't pump bar N+1
    # until consumers have pulled bar N. Without this, asyncio queues
    # are unbounded and put() doesn't yield, so strategy_consumer
    # drains every bar before any fill from bar 0 has round-tripped.
    # Maxsize 4 gives a little slack for fanout to multiple queues
    # without strict lock-step.
    queue_maxsize: int = 4

    def register_strategy(
        self,
        strategy: Strategy,
        symbols: Iterable[str],
        capital_usd: float = 100_000.0,
    ) -> None:
        """Add a strategy to the session. Wires its RiskLimits into
        the risk service and a book into the ledger."""
        if strategy.strategy_id in self.registrations:
            raise ValueError(f"strategy_id already registered: {strategy.strategy_id}")
        self.registrations[strategy.strategy_id] = StrategyRegistration(
            strategy=strategy,
            symbols=set(symbols),
            capital_usd=capital_usd,
        )
        # RiskService needs the RiskLimits object; auto-create one if
        # the strategy didn't bring its own so the gate is always
        # populated (otherwise unknown_strategy rejects fire).
        limits = strategy.risk if strategy.risk is not None else RiskLimits()
        strategy.risk = limits
        self.risk.register(strategy.strategy_id, limits)
        self.ledger.register(strategy.strategy_id)

    async def run(self, session_date: datetime) -> dict:
        """End-to-end session. Returns the Ledger snapshot at session
        close — the canonical "what did each strategy do today" record.
        """
        if not self.registrations:
            raise RuntimeError("Engine.run: no strategies registered")

        # Wire RiskService's state-provider so its capital/mark/positions
        # come from the live engine state instead of test defaults.
        self.risk.state_provider = self._risk_context_for
        # Ledger needs the back-reference so daily-loss caps trip.
        self.ledger.risk_service = self.risk

        for reg in self.registrations.values():
            reg.strategy.on_session_start(session_date)

        # ----- Queues that connect the services ----------------------
        bus_out = asyncio.Queue(maxsize=self.queue_maxsize)
        intent_q = asyncio.Queue(maxsize=self.queue_maxsize)
        approved_q = asyncio.Queue(maxsize=self.queue_maxsize)
        rejected_q = asyncio.Queue(maxsize=self.queue_maxsize)
        fill_q = asyncio.Queue(maxsize=self.queue_maxsize)
        # The router and ledger both need bars. Each gets its own queue
        # populated by the fanout — sharing one queue would mean only
        # one of them sees each bar.
        router_bar_q = asyncio.Queue(maxsize=self.queue_maxsize)
        ledger_bar_q = asyncio.Queue(maxsize=self.queue_maxsize)
        # One per-strategy bar queue so each strategy drains at its
        # own pace without blocking the others.
        per_strategy_bar_q: dict[str, asyncio.Queue] = {
            sid: asyncio.Queue(maxsize=self.queue_maxsize)
            for sid in self.registrations
        }
        # One per-strategy FILL queue. The strategy consumer drains
        # this BEFORE each on_bar so the strategy's position object
        # is fully consistent before it makes a decision. Closes the
        # bar-vs-fill race where on_bar(N+1) ran with stale state
        # because bar N's fill hadn't applied yet.
        self._per_strategy_fill_q = {
            sid: asyncio.Queue(maxsize=self.queue_maxsize)
            for sid in self.registrations
        }
        # Engine-level shutdown (separate from the in-band ShutdownEvent
        # propagation so an operator-triggered halt is distinguishable
        # from a clean end-of-stream).
        shutdown_q = asyncio.Queue(maxsize=self.queue_maxsize)

        tasks: list[asyncio.Task] = []

        # ----- Bus → fanout -----------------------------------------
        tasks.append(asyncio.create_task(
            self.bus.run(bus_out, shutdown_q),
            name=f"bus:{self.bus.name}",
        ))
        tasks.append(asyncio.create_task(
            self._bar_fanout(
                bus_out=bus_out,
                router_q=router_bar_q,
                ledger_q=ledger_bar_q,
                per_strategy_q=per_strategy_bar_q,
            ),
            name="bar_fanout",
        ))

        # ----- Strategy consumers -----------------------------------
        # Kept in a sub-list so an aux coordinator can wait for ALL of
        # them to drain their bar queues, then poison intent_q. Without
        # this the RiskService blocks forever on intent_q.get() after
        # bars run out.
        strategy_tasks: list[asyncio.Task] = []
        for sid, reg in self.registrations.items():
            t = asyncio.create_task(
                self._strategy_consumer(
                    reg,
                    per_strategy_bar_q[sid],
                    self._per_strategy_fill_q[sid],
                    intent_q,
                ),
                name=f"strategy:{sid}",
            )
            strategy_tasks.append(t)
            tasks.append(t)
        tasks.append(asyncio.create_task(
            self._close_intent_queue_after(strategy_tasks, intent_q),
            name="intent_q_closer",
        ))

        # ----- Risk → Router → Ledger -------------------------------
        tasks.append(asyncio.create_task(
            self.risk.run(intent_q, approved_q, rejected_q, shutdown_q),
            name="risk",
        ))
        tasks.append(asyncio.create_task(
            self.router.run(approved_q, router_bar_q, fill_q, shutdown_q),
            name="router",
        ))
        tasks.append(asyncio.create_task(
            self._fill_to_strategy_and_ledger(fill_q),
            name="fill_dispatch",
        ))
        tasks.append(asyncio.create_task(
            self._drain_rejections(rejected_q),
            name="reject_audit",
        ))
        # Ledger consumes its own copy of the bar feed for marks.
        tasks.append(asyncio.create_task(
            self.ledger.run(self._ledger_fill_proxy_queue(),
                            ledger_bar_q, shutdown_q),
            name="ledger",
        ))

        try:
            await asyncio.gather(*tasks)
        except Exception:
            log.exception("engine: task crashed — cancelling remaining tasks")
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise
        finally:
            for reg in self.registrations.values():
                try:
                    reg.strategy.on_session_end(session_date)
                except Exception:
                    # Surface the assertion failure but don't lose
                    # the ledger snapshot — operator still needs to
                    # see what filled before the bug.
                    log.exception(
                        "strategy %s on_session_end raised", reg.strategy.strategy_id,
                    )

        snapshot = self.ledger.to_snapshot()
        self.attach_decisions(snapshot)
        self.attach_bars(snapshot)
        return snapshot

    def attach_decisions(self, snapshot: dict, *, limit: int = 50) -> None:
        """Inject each strategy's recent decision trace into the
        ledger snapshot. Lives on the engine (not the ledger) because
        the ledger only knows strategy_ids — strategy instances live
        in registrations here. Callers that re-snapshot after run()
        (e.g. CLI rebuilds with include_fills) must re-apply this."""
        for entry in snapshot.get("strategies", []):
            sid = entry.get("strategy_id")
            reg = self.registrations.get(sid)
            if reg is None:
                entry["decisions"] = []
                continue
            entry["decisions"] = reg.strategy.recent_decisions(limit=limit)

    def attach_bars(self, snapshot: dict, *, limit: int = 300) -> None:
        """Inject each strategy's recently-seen bars into the snapshot
        so the UI can render a "what data fed in" tab alongside the
        decisions trace. Same re-apply rule as attach_decisions."""
        for entry in snapshot.get("strategies", []):
            sid = entry.get("strategy_id")
            reg = self.registrations.get(sid)
            if reg is None:
                entry["bars_seen"] = []
                continue
            entry["bars_seen"] = reg.strategy.recent_bars(limit=limit)

    # ----- Internal coroutines -------------------------------------

    async def _bar_fanout(
        self,
        bus_out: asyncio.Queue,
        router_q: asyncio.Queue,
        ledger_q: asyncio.Queue,
        per_strategy_q: dict[str, asyncio.Queue],
    ) -> None:
        """The bus emits one bar; every interested consumer needs to
        see its OWN copy. Strategy fanout filters by subscribed symbol
        so a strategy trading AAPL doesn't drain the queue checking
        TSLA bars."""
        while True:
            msg = await bus_out.get()
            if isinstance(msg, ShutdownEvent):
                # Propagate to every downstream queue so each consumer
                # drains and exits.
                await router_q.put(msg)
                await ledger_q.put(msg)
                for q in per_strategy_q.values():
                    await q.put(msg)
                return
            assert isinstance(msg, BarEvent)
            await router_q.put(msg)
            await ledger_q.put(msg)
            symbol = msg.bar.symbol
            for sid, q in per_strategy_q.items():
                if symbol in self.registrations[sid].symbols:
                    await q.put(msg)

    async def _close_intent_queue_after(
        self,
        strategy_tasks: list[asyncio.Task],
        intent_q: asyncio.Queue,
    ) -> None:
        """Wait until every strategy consumer has drained its bar
        queue, then push a ShutdownEvent on the shared intent queue.
        That terminates the RiskService, which in turn shuts down the
        router's approved-consumer and the rejection-audit task."""
        await asyncio.gather(*strategy_tasks, return_exceptions=True)
        await intent_q.put(ShutdownEvent(reason="all strategies done"))
        # Also poison every per-strategy fill queue so any straggler
        # fills don't keep a strategy_consumer stuck if we ever change
        # the consumer to await fills (currently a non-blocking drain).
        for q in self._per_strategy_fill_q.values():
            await q.put(ShutdownEvent(reason="all strategies done"))

    async def _strategy_consumer(
        self,
        reg: StrategyRegistration,
        bar_q: asyncio.Queue,
        fill_q: asyncio.Queue,
        intent_q: asyncio.Queue,
    ) -> None:
        """Pull bars for this strategy, call on_bar, publish any
        orders it returns as `OrderIntent` events.

        Before each on_bar, drain any pending fills for this strategy
        so its position object reflects every fill that has cleared.
        This is what closes the bar-vs-fill race: without this drain,
        bar N+1 could reach the strategy while bar N's fill was still
        in flight, and the strategy would re-emit because pos.is_flat
        looked True.
        """
        strategy = reg.strategy
        while True:
            msg = await bar_q.get()
            if isinstance(msg, ShutdownEvent):
                # Drain any final fills that may have landed.
                self._drain_strategy_fills(strategy, fill_q)
                return
            assert isinstance(msg, BarEvent)
            if strategy.risk is not None and strategy.risk.halted:
                continue
            # Apply any fills that landed since the last bar BEFORE
            # giving the strategy a chance to decide on this bar.
            self._drain_strategy_fills(strategy, fill_q)
            # Record the bar for the snapshot's "bars_seen" trace
            # before handing it to on_bar so post-mortem matches
            # exactly what the strategy saw.
            strategy.record_bar(msg.bar)
            try:
                orders = strategy.on_bar(msg.bar) or []
            except Exception:
                log.exception("strategy %s on_bar raised", strategy.strategy_id)
                continue
            for order in orders:
                if order.strategy_id != strategy.strategy_id:
                    log.warning(
                        "strategy %s emitted order with sid=%s; rewriting",
                        strategy.strategy_id, order.strategy_id,
                    )
                    order.strategy_id = strategy.strategy_id
                strategy.mark_order_in_flight(order.symbol)
                await intent_q.put(OrderIntent(order=order, bar_at_emit=msg.bar))
            if orders:
                # Yield so the rest of the chain (risk → router → fill
                # dispatch) gets CPU time before we move to the next bar.
                # Without this, unbounded queues let strategy_consumer
                # drain ALL bars before any fill can round-trip back,
                # and the in-flight guard + drain-before-on_bar logic
                # never actually sees the fill.
                await asyncio.sleep(0)
                # Drain any fills that landed during the yield so the
                # strategy's next on_bar sees the updated position.
                self._drain_strategy_fills(strategy, fill_q)

    def _drain_strategy_fills(self, strategy: Strategy, fill_q: asyncio.Queue) -> None:
        """Non-blocking drain of this strategy's pending fill queue.
        Synchronous because asyncio.Queue exposes get_nowait()."""
        while True:
            try:
                msg = fill_q.get_nowait()
            except asyncio.QueueEmpty:
                return
            if isinstance(msg, ShutdownEvent):
                return
            assert isinstance(msg, FillEvent)
            fill = msg.fill
            self._apply_fill_to_strategy(strategy, fill)
            strategy.clear_order_in_flight(fill.symbol)
            try:
                strategy.on_fill(fill)
            except Exception:
                log.exception("strategy %s on_fill raised", strategy.strategy_id)

    async def _fill_to_strategy_and_ledger(self, fill_q: asyncio.Queue) -> None:
        """Router publishes one FillEvent per fill; the strategy's
        position objects + the ledger's books BOTH need it. We
        intercept here, apply the fill to the strategy's position,
        call its on_fill hook, then forward to the Ledger.

        The Ledger has its own internal fill_queue (see
        `_ledger_fill_proxy_queue`) so it can run as a free-standing
        service without coupling to this dispatch logic."""
        ledger_in = self._ledger_fill_proxy_queue()
        while True:
            msg = await fill_q.get()
            if isinstance(msg, ShutdownEvent):
                await ledger_in.put(msg)
                return
            assert isinstance(msg, FillEvent)
            fill = msg.fill
            reg = self.registrations.get(fill.strategy_id)
            if reg is not None:
                # Hand the fill to the strategy's OWN queue rather
                # than applying inline. The strategy_consumer drains
                # this queue BEFORE each on_bar so the strategy's
                # position is always consistent at decision time.
                q = self._per_strategy_fill_q.get(fill.strategy_id)
                if q is not None:
                    await q.put(msg)
            # Ledger always sees every fill via its own queue.
            await ledger_in.put(msg)

    _ledger_fill_q: asyncio.Queue | None = None

    def _ledger_fill_proxy_queue(self) -> asyncio.Queue:
        """Lazy-create the ledger's fill input queue. Used by both
        `_fill_to_strategy_and_ledger` (producer) and
        `Ledger.run` (consumer). Lazy so a fresh queue is bound to
        the active event loop the first time the engine asks for it."""
        if self._ledger_fill_q is None:
            self._ledger_fill_q = asyncio.Queue(maxsize=self.queue_maxsize)
        return self._ledger_fill_q

    async def _drain_rejections(self, rejected_q: asyncio.Queue) -> None:
        """Audit channel — every rejection gets logged at WARNING.
        Once a Redis/Postgres audit sink exists, this is where to
        publish, but logging keeps the dev loop tight."""
        while True:
            msg = await rejected_q.get()
            if isinstance(msg, ShutdownEvent):
                return
            assert isinstance(msg, OrderRejected)
            log.warning(
                "order rejected · strategy=%s · symbol=%s · code=%s · %s",
                msg.order.strategy_id, msg.order.symbol, msg.code, msg.reason,
            )

    # ----- Helpers for risk / fills --------------------------------

    def _risk_context_for(self, strategy_id: str) -> RiskContext:
        """Build the RiskContext the risk service needs. Pulls the
        strategy's current positions + its capital allocation + the
        last-seen mark from the ledger."""
        reg = self.registrations[strategy_id]
        # Mark = last close the ledger saw. For a brand-new session
        # before any bar arrives, fall back to 0 — the order can't
        # have come from a strategy yet either, so the sizing checks
        # only fire after the bus emits at least one bar.
        book = self.ledger.books.get(strategy_id)
        marks = (
            [p.last_mark for p in book.positions.values() if p.last_mark > 0]
            if book is not None
            else []
        )
        mark = marks[-1] if marks else 0.0
        return RiskContext(
            strategy_capital_usd=reg.capital_usd,
            mark_price=mark,
            current_positions=dict(reg.strategy.positions),
            now=datetime.now(timezone.utc),
        )

    @staticmethod
    def _apply_fill_to_strategy(strategy: Strategy, fill) -> None:
        """Mirror the fill into the strategy's own Position object.
        This is the "engine reconciles position before next on_bar"
        contract documented in strategy.py.

        The math intentionally mirrors `Ledger._update_position_and_realise`
        for the side effects strategies care about (quantity +
        avg_entry_price). Strategies don't see realised P&L — that
        lives in the Ledger.
        """
        from .strategy import OrderSide
        pos = strategy.position_for(fill.symbol)
        delta = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
        cur_qty = pos.quantity

        if cur_qty == 0:
            pos.quantity = delta
            pos.avg_entry_price = fill.fill_price
            pos.opened_at = fill.fill_time
            return

        same_direction = (cur_qty > 0 and delta > 0) or (cur_qty < 0 and delta < 0)
        if same_direction:
            new_qty = cur_qty + delta
            pos.avg_entry_price = (
                pos.avg_entry_price * abs(cur_qty) + fill.fill_price * abs(delta)
            ) / abs(new_qty)
            pos.quantity = new_qty
            return

        # Opposite direction: partial-close or flip.
        if abs(delta) <= abs(cur_qty):
            pos.quantity = cur_qty + delta
            if pos.quantity == 0:
                pos.avg_entry_price = 0.0
                pos.opened_at = None
            return
        # Flip through zero.
        pos.quantity = delta + cur_qty
        pos.avg_entry_price = fill.fill_price
        pos.opened_at = fill.fill_time


__all__ = ["Engine", "StrategyRegistration"]
