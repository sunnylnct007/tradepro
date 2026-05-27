"""quant_engine — sleeve portfolio, vol targeting, walk-forward, Monte Carlo
and FX mean-reversion.

Public API:

  QuantEngineConfig        — all tunable parameters
  portfolio_metrics        — sharpe, sortino, max_drawdown, calmar, cagr, omega, summarise
  vol_targeting            — vol_target_scalar, apply_vol_target
  RegimeFilter             — SPY 200-SMA bull/bear gate
  Sleeve, SleeveResult     — single-sleeve Ichimoku backtest
  Ensemble, EnsembleResult — equal-weight sleeve combine + vol targeting
  WalkForwardValidator, WalkForwardWindow — rolling train/test OOS
  MonteCarloSimulator, MonteCarloResult  — block-bootstrap Monte Carlo
  FXMeanReversionStrategy, FXBacktestResult — G10 FX intraday strategy
  G10_PAIRS, HORIZONS, SMOOTHS, POS_CAP, BARS_PER_YEAR

Import examples::

    from tradepro_strategies.quant_engine import QuantEngineConfig, Ensemble
    from tradepro_strategies.quant_engine.portfolio_metrics import summarise
"""
from __future__ import annotations

from .config import QuantEngineConfig
from .ensemble import Ensemble, EnsembleResult
from .fx_strategy import (
    BARS_PER_YEAR,
    FXBacktestResult,
    FXMeanReversionStrategy,
    G10_PAIRS,
    HORIZONS,
    POS_CAP,
    SMOOTHS,
)
from .monte_carlo import MonteCarloResult, MonteCarloSimulator
from .portfolio_metrics import (
    cagr,
    calmar,
    max_drawdown,
    omega,
    sharpe,
    sortino,
    summarise,
)
from .regime_filter import RegimeFilter
from .sleeve import Sleeve, SleeveResult
from .universe_builder import BetaResult, build_high_beta, compute_beta
from .vol_targeting import apply_vol_target, vol_target_scalar
from .walk_forward import WalkForwardValidator, WalkForwardWindow

__all__ = [
    "QuantEngineConfig",
    # portfolio_metrics
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "cagr",
    "omega",
    "summarise",
    # vol_targeting
    "vol_target_scalar",
    "apply_vol_target",
    # regime_filter
    "RegimeFilter",
    # sleeve
    "Sleeve",
    "SleeveResult",
    # universe_builder
    "BetaResult",
    "build_high_beta",
    "compute_beta",
    # ensemble
    "Ensemble",
    "EnsembleResult",
    # walk_forward
    "WalkForwardValidator",
    "WalkForwardWindow",
    # monte_carlo
    "MonteCarloSimulator",
    "MonteCarloResult",
    # fx_strategy
    "FXMeanReversionStrategy",
    "FXBacktestResult",
    "G10_PAIRS",
    "HORIZONS",
    "SMOOTHS",
    "POS_CAP",
    "BARS_PER_YEAR",
]
