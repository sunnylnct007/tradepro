"""Walk-forward OOS (out-of-sample) validation.

For each (train_start, train_end, test_year) window:
  1. Estimate the vol scalar on the TRAIN window (single scalar =
     target_vol / train_std, capped at max_leverage).
  2. Apply the fixed scalar to the TEST window returns.
  3. Record test_sharpe, test_cagr_pct, n_test_days.

This validates that the vol-targeting regime discovered on train data
still works OOS — avoids overfitting to a single in-sample period.

Note: the walk-forward windows default to the config definition
(5 windows, 2018-2025). Window 5 tests 2025 which is a partial year
as of 2026-05 — see QUANT_ENGINE_GAPS.md Gap 7.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .portfolio_metrics import sharpe as _sharpe, cagr as _cagr


@dataclass
class WalkForwardWindow:
    """Results for one train/test split."""
    test_year: str
    vol_scalar: float      # constant scalar estimated on train data
    test_sharpe: float
    test_cagr_pct: float
    n_test_days: int


class WalkForwardValidator:
    """Rolling train/test walk-forward for a daily returns series.

    Parameters
    ----------
    returns : pd.Series
        Daily returns with DatetimeIndex covering at least the full
        date range implied by config.walk_forward_windows.
    target_vol : float
        Annualised vol target. Default 0.12.
    max_leverage : float
        Cap on the scalar. Default 1.5.
    windows : sequence or None
        List of (train_start, train_end, test_year) tuples. If None,
        reads from QuantEngineConfig.
    """

    def __init__(
        self,
        returns: pd.Series,
        target_vol: float = 0.12,
        max_leverage: float = 1.5,
        windows=None,
    ) -> None:
        self.returns = returns
        self.target_vol = target_vol
        self.max_leverage = max_leverage

        if windows is None:
            from .config import QuantEngineConfig
            windows = QuantEngineConfig().walk_forward_windows
        self.windows = windows

    def run(self) -> tuple[pd.Series, list[WalkForwardWindow]]:
        """Run all windows and return (oos_returns, window_list).

        The oos_returns series is the concatenation of all test-window
        scaled returns (useful for computing a full-period OOS Sharpe).
        """
        oos_parts = []
        window_results = []

        for train_start, train_end, test_year in self.windows:
            # Train slice
            train = self.returns[train_start:train_end]
            if train.empty:
                continue
            train_std = train.std(ddof=1)
            if train_std == 0 or np.isnan(train_std):
                vol_scalar = 1.0
            else:
                train_ann_vol = train_std * np.sqrt(252)
                vol_scalar = min(self.target_vol / train_ann_vol, self.max_leverage)

            # Test slice: full calendar year
            test_start = f"{test_year}-01-01"
            test_end = f"{test_year}-12-31"
            test = self.returns[test_start:test_end]
            if test.empty:
                continue

            scaled_test = test * vol_scalar
            equity_test = (1.0 + scaled_test).cumprod() * 100_000.0

            test_sharpe = _sharpe(scaled_test)
            test_cagr = _cagr(equity_test) * 100.0

            oos_parts.append(scaled_test)
            window_results.append(WalkForwardWindow(
                test_year=str(test_year),
                vol_scalar=round(float(vol_scalar), 6),
                test_sharpe=round(float(test_sharpe), 4),
                test_cagr_pct=round(float(test_cagr), 4),
                n_test_days=len(test),
            ))

        if oos_parts:
            oos_returns = pd.concat(oos_parts).sort_index()
        else:
            oos_returns = pd.Series(dtype=float)

        return oos_returns, window_results
