"""Strategy registry. Adding a strategy is two lines: export a signal
function in its own module, and register it in REGISTRY below."""
from __future__ import annotations

from typing import Callable

import pandas as pd

from .buy_and_hold import buy_and_hold_signals
from .sma_crossover import sma_crossover_signals

SignalFn = Callable[[pd.DataFrame], pd.Series]
Factory = Callable[[dict], SignalFn]


REGISTRY: dict[str, Factory] = {
    "buy_and_hold": lambda _params: buy_and_hold_signals,
    "sma_crossover": lambda params: (
        lambda df: sma_crossover_signals(
            df,
            fast=int(params.get("fast", 20)),
            slow=int(params.get("slow", 50)),
        )
    ),
}


def resolve(name: str, params: dict | None = None) -> SignalFn:
    if name not in REGISTRY:
        raise ValueError(f"unknown strategy '{name}'. Available: {list(REGISTRY)}")
    return REGISTRY[name](params or {})


def available() -> list[str]:
    return sorted(REGISTRY)


__all__ = [
    "REGISTRY",
    "resolve",
    "available",
    "buy_and_hold_signals",
    "sma_crossover_signals",
]
