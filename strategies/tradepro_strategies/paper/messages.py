"""Event types passed between paper-trading services.

Every interaction between services (BarBus → Strategy → Risk → Router →
Ledger) flows as one of these messages over an async queue. Services
never call each other's methods directly; they read from input queues
and publish to output queues.

This shape is the load-bearing decision behind "modular monolith
today, microservices tomorrow":

- Today: queues are `asyncio.Queue`, all services run in one process.
- Tomorrow: swap to Redis Streams / NATS subjects per message type
  with the SAME dataclass shape on the wire. No business-logic
  rewrites — only the queue plumbing changes.

The classes here are intentionally frozen and JSON-serialisable so
moving them between processes (or saving them for replay) needs no
adapter code. `to_dict` / `from_dict` are the canonical wire format.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .strategy import Bar, Fill, Order, OrderSide, OrderType


class MessageKind(str, Enum):
    """Tag for serialised wire format so a multi-type queue
    consumer can route deterministically."""
    BAR = "bar"
    ORDER_INTENT = "order_intent"
    ORDER_APPROVED = "order_approved"
    ORDER_REJECTED = "order_rejected"
    FILL = "fill"
    HEARTBEAT = "heartbeat"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class BarEvent:
    """Published by the BarBus, consumed by strategy services. One
    BarEvent per (symbol, timeframe, timestamp) tuple — the bus is
    responsible for de-duping if the upstream feed double-publishes."""
    kind: MessageKind = field(default=MessageKind.BAR, init=False)
    bar: Bar
    sequence: int   # monotonic per-bus counter; lets consumers detect gaps


@dataclass(frozen=True)
class OrderIntent:
    """Published by a strategy on the strategy → risk channel. The
    strategy has decided it wants to place this order; risk has not
    yet approved. Carrying `bar_at_emit` lets the risk service apply
    point-in-time checks (vol cap based on the bar that triggered
    the order, not the bar two minutes later)."""
    kind: MessageKind = field(default=MessageKind.ORDER_INTENT, init=False)
    order: Order
    bar_at_emit: Bar


@dataclass(frozen=True)
class OrderApproved:
    """Risk service approves the intent. The router consumes these."""
    kind: MessageKind = field(default=MessageKind.ORDER_APPROVED, init=False)
    order: Order
    bar_at_approval: Bar
    approved_at: datetime


@dataclass(frozen=True)
class OrderRejected:
    """Risk service rejects the intent. Engine logs to the audit
    trail; downstream services never see it."""
    kind: MessageKind = field(default=MessageKind.ORDER_REJECTED, init=False)
    order: Order
    code: str          # machine-readable, e.g. "max_position_value"
    reason: str        # human-readable for the audit log
    rejected_at: datetime


@dataclass(frozen=True)
class FillEvent:
    """Published by the router after a fill lands (paper or real).
    Ledger consumes these to compute per-strategy P&L; strategies
    consume them via the engine's `on_fill` hook to update their
    own position dicts."""
    kind: MessageKind = field(default=MessageKind.FILL, init=False)
    fill: Fill


@dataclass(frozen=True)
class HeartbeatEvent:
    """Periodic liveness ping any service can emit. Engine aggregates
    these into a `/api/paper/status` payload so operator can see which
    services are alive."""
    kind: MessageKind = field(default=MessageKind.HEARTBEAT, init=False)
    service: str
    at: datetime
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShutdownEvent:
    """Engine broadcasts on graceful shutdown so every service drains
    its queue and exits cleanly. Services must propagate by
    re-publishing on their output queues if they have any."""
    kind: MessageKind = field(default=MessageKind.SHUTDOWN, init=False)
    reason: str = "engine shutdown"


# Wire-format helpers --------------------------------------------------

def _encode_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Enum):
        return v.value
    if hasattr(v, "__dataclass_fields__"):
        return _encode_dict(asdict(v))
    if isinstance(v, dict):
        return _encode_dict(v)
    if isinstance(v, (list, tuple)):
        return [_encode_value(x) for x in v]
    return v


def _encode_dict(d: dict) -> dict:
    return {k: _encode_value(v) for k, v in d.items()}


def to_wire(event: Any) -> dict:
    """JSON-serialisable dict for crossing a process boundary.
    Used today for log replay + tomorrow for Redis/NATS."""
    return _encode_dict(asdict(event))


def bar_from_wire(d: dict) -> Bar:
    """Reverse of to_wire for a Bar — explicit because Bar is the
    most commonly replayed message type."""
    ts = d["timestamp"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return Bar(
        symbol=d["symbol"],
        timestamp=ts,
        open=float(d["open"]),
        high=float(d["high"]),
        low=float(d["low"]),
        close=float(d["close"]),
        volume=int(d["volume"]),
        timeframe_seconds=int(d["timeframe_seconds"]),
    )


__all__ = [
    "MessageKind",
    "BarEvent",
    "OrderIntent",
    "OrderApproved",
    "OrderRejected",
    "FillEvent",
    "HeartbeatEvent",
    "ShutdownEvent",
    "to_wire",
    "bar_from_wire",
]
