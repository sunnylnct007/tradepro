"""Parity guard: the intraday scanner's daily signal (signal_bridge.
ichimoku_daily_signal) MUST equal the verbatim trader port
(_equity_trader_signal.latest_signal_and_meta) — the single source of truth.

Regression for 2026-06-09: the bridge had drifted (extra `close > tenkan`
condition + stateless), so it returned FLAT for genuinely-bullish names
(NVDA/AAPL/TSLA/AMD) that the equity strategy bought — intraday silently
generated 0 orders. If these ever diverge again this test fails loudly.
"""
import numpy as np
import pandas as pd

from tradepro_strategies.paper.signal_bridge import ichimoku_daily_signal
from tradepro_strategies.paper.strategies._equity_trader_signal import (
    latest_signal_and_meta,
)


def _series(seed: int, n: int = 300):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 1.0, n).cumsum()
    close = 100.0 + steps
    high = close + np.abs(rng.normal(0.5, 0.3, n))
    low = close - np.abs(rng.normal(0.5, 0.3, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.Series(close, idx), pd.Series(high, idx), pd.Series(low, idx)


def test_bridge_matches_verbatim_across_random_walks():
    mism = []
    for seed in range(40):
        close, high, low = _series(seed)
        ib, _ = ichimoku_daily_signal(high, low, close)
        eq, _ = latest_signal_and_meta(close.values, high.values, low.values)
        if float(ib) != float(eq):
            mism.append((seed, float(eq), float(ib)))
    assert not mism, f"signal divergence (verbatim vs bridge): {mism[:5]}"
