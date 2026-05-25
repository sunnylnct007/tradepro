"""QuantEngineConfig — all tunable parameters for the quant engine.

All magic numbers and thresholds for the sleeve portfolio, vol targeting,
walk-forward validation, Monte Carlo simulation and FX mean-reversion
live here. Never hardcode inline in strategy code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class QuantEngineConfig:
    start_date: str = "2018-01-01"
    end_date: str = "2025-12-31"
    initial_capital: float = 100_000.0
    cost_bps: float = 5.0
    benchmark: str = "SPY"

    # Ichimoku parameters (adapted for daily equity — shorter than traditional)
    tenkan: int = 5
    kijun: int = 32
    senkou_b: int = 50
    displacement: int = 32

    # Sleeve sizes (number of positions in each sleeve)
    sleeve_large: int = 20
    sleeve_hibeta: int = 30
    sleeve_gold: int = 1

    # High-beta filter
    min_beta: float = 1.5
    beta_lookback: int = 252

    # Regime filter
    regime_sma: int = 200
    use_regime_filter: bool = True

    # Vol targeting (Hurst-Ooi-Pedersen 2017)
    target_vol: float = 0.12
    max_leverage: float = 1.5
    vol_lookback: int = 60

    # Walk-forward windows: (train_start, train_end, test_year_label)
    walk_forward_windows: tuple = (
        ("2018-01-01", "2020-12-31", "2021"),
        ("2019-01-01", "2021-12-31", "2022"),
        ("2020-01-01", "2022-12-31", "2023"),
        ("2021-01-01", "2023-12-31", "2024"),
        ("2022-01-01", "2024-12-31", "2025"),
    )

    # S&P 500 large-cap universe (50 names)
    large_50: tuple = (
        "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMD", "CRM", "ORCL", "ADBE", "CSCO",
        "AMZN", "TSLA", "HD", "NKE", "MCD", "SBUX", "WMT", "PG", "KO", "PEP", "COST",
        "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "JNJ", "UNH", "PFE",
        "ABBV", "LLY", "MRK", "XOM", "CVX", "COP", "BA", "CAT", "GE", "HON", "UPS",
        "DIS", "NFLX", "T", "VZ", "LIN", "NEE",
    )

    gold_tickers: tuple = ("GLD",)

    # Crypto proxies to exclude from equity sleeves
    crypto_exclude: frozenset = frozenset({
        "COIN", "MSTR", "MARA", "RIOT", "HUT", "CLSK", "WULF", "BITF", "CIFR", "IREN",
        "HIVE", "BTBT", "CAN", "EBON", "GREE", "BTDR", "APLD", "GLXY", "GBTC", "BITO",
        "ETHE", "IBIT", "FBTC", "BLOK", "BITQ", "ARKB", "BITX", "BKKT", "HOOD", "SQ", "PYPL",
    })

    # Annualisation factor: sqrt(252) for daily returns
    ann_factor: float = field(default_factory=lambda: float(np.sqrt(252)))
