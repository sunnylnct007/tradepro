"""Broker integrations for the paper-trading engine.

The engine accepts any `BarBus` for market data and any `OrderRouter`
for order execution. This package is where the concrete broker
implementations live, behind hard service boundaries.

Today's roster:
  - T212OrderRouter        — Trading 212 REST (orders only; pair with
                             yfinance for bars since T212's API has no
                             OHLC / quotes endpoint)
  - IBKRBarBus, IBKRRouter — Interactive Brokers via ib_insync;
                             gateway-backed bars + orders

Both impls are SAFE-BY-DEFAULT: they refuse to place real-money
orders unless the operator opts in explicitly via constructor flag
AND environment variable. This is the same posture the .NET backend
takes for T212 — order placement off until a one-button UI safety
story is in place. The paper engine inherits that posture.

Pair these with `paper.profiles.build_session(broker=...)` if you
want a one-call factory instead of constructing bus + router by hand.
"""
from .t212 import T212OrderRouter
from .ibkr import IBKRBarBus, IBKRRouter

__all__ = [
    "T212OrderRouter",
    "IBKRBarBus",
    "IBKRRouter",
]
