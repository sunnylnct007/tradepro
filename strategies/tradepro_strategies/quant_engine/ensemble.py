"""Ensemble of sleeves: equal-weight combine + vol targeting.

The ensemble combines multiple sleeves into a single portfolio:
  1. Each sleeve contributes equally (1/N weight).
  2. The combined daily returns are passed through the vol-targeting
     scalar so the ensemble targets a constant annualised volatility.
  3. Metrics are computed on the final vol-targeted equity curve.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .portfolio_metrics import summarise
from .sleeve import Sleeve, SleeveResult
from .vol_targeting import apply_vol_target


@dataclass
class EnsembleResult:
    """Output from an Ensemble backtest run."""
    equity: pd.Series
    daily_returns: pd.Series
    sleeve_returns: dict[str, pd.Series]   # name → raw sleeve returns
    vol_scalar: pd.Series
    summary: dict                          # from portfolio_metrics.summarise()


class Ensemble:
    """Equal-weight sleeve combine with vol targeting.

    Parameters
    ----------
    sleeves : list[Sleeve]
        Pre-constructed Sleeve objects. At least one required.
    config : QuantEngineConfig or None
        Config dataclass. If None, uses defaults.
    initial_capital : float
        Starting value for the equity curve. Default 100_000.
    """

    def __init__(
        self,
        sleeves: list[Sleeve],
        config=None,
        initial_capital: float = 100_000.0,
    ) -> None:
        if not sleeves:
            raise ValueError("Ensemble requires at least one sleeve.")
        self.sleeves = sleeves
        self.initial_capital = initial_capital

        if config is None:
            from .config import QuantEngineConfig
            config = QuantEngineConfig()
        self.config = config

    def run(self) -> EnsembleResult:
        """Run all sleeves and combine into a vol-targeted portfolio."""
        cfg = self.config
        results: list[SleeveResult] = [s.run() for s in self.sleeves]

        # Align all sleeve returns to the union of their indices
        sleeve_series = {r.name: r.returns for r in results}
        combined_df = pd.DataFrame(sleeve_series).fillna(0.0)

        # Equal-weight combine
        n = len(results)
        portfolio_raw = combined_df.mean(axis=1)

        # Vol targeting
        scaled, scalar = apply_vol_target(
            portfolio_raw,
            target_vol=cfg.target_vol,
            max_leverage=cfg.max_leverage,
            lookback=cfg.vol_lookback,
        )

        equity = (1.0 + scaled).cumprod() * self.initial_capital
        summary = summarise(equity, scaled)

        return EnsembleResult(
            equity=equity,
            daily_returns=scaled,
            sleeve_returns=sleeve_series,
            vol_scalar=scalar,
            summary=summary,
        )
