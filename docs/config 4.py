# ============================================================
# config.py
# ============================================================
"""All tunable parameters as a single dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Config:
    """Single source of truth for all strategy parameters."""

    # Backtest window
    start_date: str = "2018-01-01"
    end_date: str = "2025-12-31"

    # Portfolio basics
    initial_capital: float = 100_000.0
    cost_bps: float = 5.0
    benchmark: str = "SPY"

    # Ichimoku (optimised vs classic 9, 26, 52, 26)
    tenkan: int = 5
    kijun: int = 32
    senkou_b: int = 50
    displacement: int = 32

    # Sleeve sizing (each signal gets 1/sleeve_size of sleeve capital)
    sleeve_large: int = 20
    sleeve_hibeta: int = 30
    sleeve_gold: int = 1

    # Universe filters
    min_beta: float = 1.5
    beta_lookback: int = 252

    # Regime filter
    regime_sma: int = 200
    use_regime_filter: bool = True

    # Ensemble vol targeting (Hurst-Ooi-Pedersen 2017)
    target_vol: float = 0.12
    max_leverage: float = 1.5
    vol_lookback: int = 60

    # Constants
    ann_factor: float = field(default_factory=lambda: float(np.sqrt(252)))

    crypto_exclude: frozenset[str] = frozenset({
        "COIN","MSTR","MARA","RIOT","HUT","CLSK","WULF","BITF","CIFR","IREN",
        "HIVE","BTBT","CAN","EBON","GREE","BTDR","APLD","GLXY","GBTC","BITO",
        "ETHE","IBIT","FBTC","BLOK","BITQ","ARKB","BITX","BKKT","HOOD","SQ","PYPL",
    })

    large_50: tuple[str, ...] = (
        "AAPL","MSFT","GOOGL","META","NVDA","AMD","CRM","ORCL","ADBE","CSCO",
        "AMZN","TSLA","HD","NKE","MCD","SBUX","WMT","PG","KO","PEP","COST",
        "JPM","BAC","WFC","GS","MS","V","MA","AXP","BLK","JNJ","UNH","PFE",
        "ABBV","LLY","MRK","XOM","CVX","COP","BA","CAT","GE","HON","UPS",
        "DIS","NFLX","T","VZ","LIN","NEE",
    )

    gold_tickers: tuple[str, ...] = ("GLD",)

    # Walk-forward windows: (train_start, train_end, test_year)
    walk_forward_windows: tuple = (
        ("2018-01-01", "2020-12-31", "2021"),
        ("2019-01-01", "2021-12-31", "2022"),
        ("2020-01-01", "2022-12-31", "2023"),
        ("2021-01-01", "2023-12-31", "2024"),
        ("2022-01-01", "2024-12-31", "2025"),
    )
