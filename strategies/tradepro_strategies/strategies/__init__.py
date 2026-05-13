"""Strategy registry. Adding a strategy is two lines: export a signal
function in its own module, and register it in REGISTRY below."""
from __future__ import annotations

from typing import Callable

import pandas as pd

from .buy_and_hold import buy_and_hold_signals
from .donchian_breakout import donchian_breakout_signals
from .ichimoku_cloud import ichimoku_cloud_signals, ichimoku_targets
from .macd_signal_cross import macd_signal_cross_signals
from .rsi_mean_reversion import rsi_mean_reversion_signals
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
    "rsi_mean_reversion": lambda params: (
        lambda df: rsi_mean_reversion_signals(
            df,
            period=int(params.get("period", 14)),
            low=float(params.get("low", 30)),
            high=float(params.get("high", 70)),
        )
    ),
    "macd_signal_cross": lambda params: (
        lambda df: macd_signal_cross_signals(
            df,
            fast=int(params.get("fast", 12)),
            slow=int(params.get("slow", 26)),
            signal=int(params.get("signal", 9)),
        )
    ),
    "donchian_breakout": lambda params: (
        lambda df: donchian_breakout_signals(
            df,
            lookback=int(params.get("lookback", 20)),
        )
    ),
    "ichimoku_cloud": lambda params: (
        lambda df: ichimoku_cloud_signals(
            df,
            tenkan=int(params.get("tenkan", 9)),
            kijun=int(params.get("kijun", 26)),
            senkou_b=int(params.get("senkou_b", 52)),
            displacement=int(params.get("displacement", 26)),
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
    "rsi_mean_reversion_signals",
    "macd_signal_cross_signals",
    "donchian_breakout_signals",
    "ichimoku_cloud_signals",
    "ichimoku_targets",
]
