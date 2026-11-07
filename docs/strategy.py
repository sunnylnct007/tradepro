# ============================================================
# strategy.py
# ============================================================
"""Strategy components: Ichimoku signals, regime filter, universe building."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import Config
from data import DataFetcher


class IchimokuStrategy:
    """Ichimoku Cloud indicator + long/flat signal generation."""

    def __init__(self, config: Config) -> None:
        self.cfg = config

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add tenkan, kijun, span_a/b, cloud_top/bottom columns."""
        df = df.copy()
        cfg = self.cfg
        df["tenkan"] = (
            df["High"].rolling(cfg.tenkan).max() + df["Low"].rolling(cfg.tenkan).min()
        ) / 2
        df["kijun"] = (
            df["High"].rolling(cfg.kijun).max() + df["Low"].rolling(cfg.kijun).min()
        ) / 2
        df["span_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(cfg.displacement)
        df["span_b"] = ((
            df["High"].rolling(cfg.senkou_b).max()
            + df["Low"].rolling(cfg.senkou_b).min()
        ) / 2).shift(cfg.displacement)
        df["cloud_top"] = df[["span_a", "span_b"]].max(axis=1)
        df["cloud_bottom"] = df[["span_a", "span_b"]].min(axis=1)
        return df

    def compute_position(self, df: pd.DataFrame) -> pd.DataFrame:
        """Stateful long/flat. ENTRY: Close>cloud AND TK bullish. EXIT: opposite."""
        df = df.copy()
        long_cond = (df["Close"] > df["cloud_top"]) & (df["tenkan"] > df["kijun"])
        flat_cond = (df["Close"] < df["cloud_bottom"]) | (df["tenkan"] < df["kijun"])
        pos = pd.Series(np.nan, index=df.index)
        pos[long_cond] = 1.0
        pos[flat_cond] = 0.0
        df["position"] = pos.ffill().fillna(0.0)
        return df

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Shortcut: compute indicators then signals."""
        return self.compute_position(self.compute_indicators(df))


class RegimeFilter:
    """SPY-based bull/bear regime detection (200-day SMA)."""

    def __init__(self, spy_df: pd.DataFrame, sma_period: int = 200) -> None:
        close = spy_df["Close"]
        self.bull_mask: pd.Series = (close > close.rolling(sma_period).mean()).fillna(False)

    def align(self, dates) -> pd.Series:
        """Align the bull mask to a given date index."""
        return self.bull_mask.reindex(dates).fillna(False)


class UniverseBuilder:
    """Construct the high-beta sleeve universe (β > min vs SPY, ex-crypto)."""

    def __init__(self, fetcher: DataFetcher, config: Config) -> None:
        self.fetcher = fetcher
        self.cfg = config

    @staticmethod
    def _compute_beta(stock_returns: pd.Series, market_returns: pd.Series,
                      min_obs: int) -> float:
        common = stock_returns.index.intersection(market_returns.index)
        if len(common) < min_obs:
            return np.nan
        s, m = stock_returns.loc[common].values, market_returns.loc[common].values
        var_m = np.var(m)
        return np.cov(s, m)[0, 1] / var_m if var_m > 0 else np.nan

    def build_high_beta(self, spy_df: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
        """Scrape candidates, filter to β > min_beta, exclude crypto."""
        print("Scraping S&P 500 + 400 from Wikipedia...")
        candidates = sorted(set(self.fetcher.fetch_index_tickers()) - self.cfg.crypto_exclude)
        print(f"  Candidate pool: {len(candidates)} (after crypto exclusion)")

        spy_returns = spy_df["Close"].pct_change().dropna()
        print("Fetching candidates & computing betas...")

        data: dict[str, pd.DataFrame] = {}
        betas: dict[str, float] = {}
        for i, t in enumerate(candidates):
            if i and i % 200 == 0:
                print(f"  Processed {i}/{len(candidates)}...")
            df = self.fetcher.fetch(t)
            if df is None or len(df) < self.cfg.beta_lookback + 50:
                continue
            beta = self._compute_beta(
                df["Close"].pct_change().dropna(), spy_returns, self.cfg.beta_lookback,
            )
            if not np.isnan(beta) and beta > self.cfg.min_beta:
                data[t] = df
                betas[t] = beta

        print(f"  High-beta universe: {len(data)} stocks (β > {self.cfg.min_beta})")
        return data, betas
