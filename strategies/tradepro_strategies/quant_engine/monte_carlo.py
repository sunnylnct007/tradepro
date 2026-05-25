"""Block-bootstrap Monte Carlo simulation.

Draws random blocks of consecutive daily returns (block_size=21 ≈ 1
trading month) and assembles them into synthetic equity paths. This
preserves the autocorrelation and fat-tail structure of actual returns
better than IID sampling.

Each path starts at `initial` and compounds the drawn returns. The
summary dictionary reports the distribution of final portfolio values
and maximum drawdowns across all simulated paths.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .portfolio_metrics import max_drawdown as _max_dd, cagr as _cagr


@dataclass
class MonteCarloResult:
    """Output from a Monte Carlo simulation run."""
    paths: np.ndarray          # shape (n_sims, n_days+1) — equity paths
    summary: dict
    n_sims: int
    years: int


class MonteCarloSimulator:
    """Block-bootstrap Monte Carlo simulator.

    Parameters
    ----------
    returns : pd.Series
        Daily returns (used as the empirical distribution to sample from).
    block_size : int
        Length of each bootstrap block in trading days. Default 21.
    seed : int or None
        Random seed for reproducibility.
    """

    def __init__(
        self,
        returns: pd.Series,
        block_size: int = 21,
        seed: int | None = None,
    ) -> None:
        self.returns = returns.dropna().values
        self.block_size = block_size
        self.seed = seed

    def run(
        self,
        initial: float = 10_000.0,
        years: int = 10,
        n_sims: int = 1000,
        periods_per_year: int = 252,
    ) -> MonteCarloResult:
        """Run the simulation and return a MonteCarloResult.

        Parameters
        ----------
        initial : float
            Starting portfolio value.
        years : int
            Simulation horizon in years.
        n_sims : int
            Number of simulation paths.
        periods_per_year : int
            Trading days per year (252 for daily).
        """
        rng = np.random.default_rng(self.seed)
        n_days = int(years * periods_per_year)
        src = self.returns
        n_src = len(src)
        bs = self.block_size

        if n_src == 0:
            raise ValueError("returns series is empty")

        # Build paths array: shape (n_sims, n_days+1)
        paths = np.empty((n_sims, n_days + 1))
        paths[:, 0] = initial

        for sim_idx in range(n_sims):
            daily = np.empty(n_days)
            filled = 0
            while filled < n_days:
                # Draw a random starting position for the block
                start = int(rng.integers(0, max(1, n_src - bs + 1)))
                block = src[start: start + bs]
                take = min(len(block), n_days - filled)
                daily[filled: filled + take] = block[:take]
                filled += take
            # Compound
            paths[sim_idx, 1:] = initial * np.cumprod(1.0 + daily)

        final_values = paths[:, -1]
        summary = self._compute_summary(paths, final_values, initial, years, periods_per_year)

        return MonteCarloResult(
            paths=paths,
            summary=summary,
            n_sims=n_sims,
            years=years,
        )

    def _compute_summary(
        self,
        paths: np.ndarray,
        final_values: np.ndarray,
        initial: float,
        years: int,
        periods_per_year: int,
    ) -> dict:
        pct_labels = [5, 10, 25, 50, 75, 90, 95]
        percentiles_vals = np.percentile(final_values, pct_labels)

        def _cagr_from_fv(fv: float) -> float:
            if initial <= 0 or years <= 0:
                return 0.0
            return float((fv / initial) ** (1.0 / years) - 1.0) * 100.0

        percentiles = {}
        for pct, fv in zip(pct_labels, percentiles_vals):
            percentiles[f"p{pct}"] = {
                "final_value": round(float(fv), 2),
                "multiple": round(float(fv / initial), 4) if initial > 0 else 0.0,
                "cagr_pct": round(_cagr_from_fv(fv), 4),
            }

        # Max drawdown distribution across paths
        mdd_per_path = []
        for i in range(paths.shape[0]):
            equity = pd.Series(paths[i])
            mdd_per_path.append(_max_dd(equity) * 100.0)  # as %
        mdd_arr = np.array(mdd_per_path)

        max_dd_pct = {
            f"p{p}": round(float(np.percentile(mdd_arr, p)), 4)
            for p in [5, 25, 50, 75, 95]
        }

        return {
            "percentiles": percentiles,
            "mean_final": round(float(final_values.mean()), 2),
            "p_lose_money": round(float((final_values < initial).mean()), 4),
            "p_double": round(float((final_values >= initial * 2).mean()), 4),
            "p_5x": round(float((final_values >= initial * 5).mean()), 4),
            "max_dd_pct": max_dd_pct,
        }
