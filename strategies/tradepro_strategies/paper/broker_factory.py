"""BrokerFactory — create the right OrderRouter by name.

Strategies never import broker modules directly — they call
`broker_factory.create_router("t212")`. This single indirection
means switching broker = changing one string, zero strategy changes.

Currently supported: "t212", "ibkr"
Adding a new broker: add one elif branch here + implement the adapter.
"""
from __future__ import annotations

from typing import Any


SUPPORTED_BROKERS = ("t212", "ibkr")


def create_router(broker: str = "t212", **kwargs: Any):
    """Instantiate and return an OrderRouter for the named broker.

    Args:
        broker: "t212" (Trading 212, default) or "ibkr" (Interactive Brokers)
        **kwargs: forwarded to the router constructor
                  T212: api_key, api_secret, mode ("demo"|"live"), allow_real_orders
                  IBKR: host, port, client_id

    Raises:
        ValueError: unknown broker name
    """
    broker = broker.lower().strip()
    if broker == "t212":
        from .brokers.t212 import T212OrderRouter
        return T212OrderRouter(**kwargs)
    elif broker == "ibkr":
        from .brokers.ibkr import IBKRRouter
        return IBKRRouter(**kwargs)
    else:
        raise ValueError(
            f"Unknown broker {broker!r}. Supported: {SUPPORTED_BROKERS}"
        )


__all__ = ["create_router", "SUPPORTED_BROKERS"]
