"""Pure portfolio metric functions — Sharpe, Sortino, MaxDD, Calmar, CAGR, Omega.

All functions are pure (no side effects, no I/O). Pass in a pd.Series
of daily returns and an equity curve (cumprod of 1+returns).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualised Sharpe ratio (mean / std * sqrt(periods_per_year)).

    Returns 0.0 when std is zero or returns is empty so callers never
    get NaN/inf in a summary dict.
    """
    if returns.empty:
        return 0.0
    mu = returns.mean()
    sigma = returns.std(ddof=1)
    if sigma == 0 or math.isnan(sigma):
        return 0.0
    return float(mu / sigma * math.sqrt(periods_per_year))


def sortino(returns: pd.Series, mar: float = 0.0, periods_per_year: int = 252) -> float:
    """Annualised Sortino ratio — penalises only downside deviation.

    mar: minimum acceptable return (daily). Default 0.
    Returns 0.0 when downside std is zero (pure upside).
    """
    if returns.empty:
        return 0.0
    mu = returns.mean()
    downside = returns[returns < mar] - mar
    downside_std = math.sqrt((downside ** 2).mean()) if len(downside) > 0 else 0.0
    if downside_std == 0 or math.isnan(downside_std):
        return 0.0
    return float(mu / downside_std * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a negative fraction.

    e.g. -0.15 means the equity curve fell 15% from its peak at some
    point. Returns 0.0 for monotone-increasing series.
    """
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    return float(dd.min())


def calmar(cagr_pct: float, max_dd_pct: float) -> float:
    """Calmar ratio: CAGR (%) / abs(MaxDD (%)).

    Guards against division by zero — returns 0.0 when max_dd_pct is
    zero (perfect uptrend). Both inputs in % terms (e.g. 15.0, -20.0).
    """
    if max_dd_pct == 0.0:
        return 0.0
    return float(cagr_pct / abs(max_dd_pct))


def cagr(equity: pd.Series, periods_per_year: int = 252) -> float:
    """Compound annual growth rate as a fraction (not %).

    Returns 0.0 for single-bar series. Uses the number of periods in
    the equity curve divided by periods_per_year to derive years.
    """
    if equity.empty or len(equity) < 2:
        return 0.0
    start = equity.iloc[0]
    end = equity.iloc[-1]
    if start <= 0:
        return 0.0
    n_years = (len(equity) - 1) / periods_per_year
    if n_years <= 0:
        return 0.0
    return float((end / start) ** (1.0 / n_years) - 1.0)


def omega(returns: pd.Series, mar: float = 0.0) -> float:
    """Omega ratio: sum of gains above MAR / sum of losses below MAR.

    Returns float('inf') when there are no losses below MAR and returns
    0.0 when there are no gains (protects against div-by-zero in both
    directions).
    """
    if returns.empty:
        return 0.0
    gains = (returns[returns > mar] - mar).sum()
    losses = (mar - returns[returns < mar]).sum()
    if losses == 0:
        return float("inf") if gains > 0 else 1.0
    return float(gains / losses)


def summarise(equity: pd.Series, returns: pd.Series,
              periods_per_year: int = 252) -> dict:
    """Compute all metrics and return as a dict.

    Keys: cagr_pct, sharpe, sortino, max_drawdown_pct, calmar, omega.

    Percentages are in % (e.g. cagr_pct=15.3, max_drawdown_pct=-18.2).
    """
    cagr_frac = cagr(equity, periods_per_year)
    cagr_pct = round(cagr_frac * 100, 4)
    mdd = max_drawdown(equity)
    mdd_pct = round(mdd * 100, 4)
    return {
        "cagr_pct": cagr_pct,
        "sharpe": round(sharpe(returns, periods_per_year), 4),
        "sortino": round(sortino(returns, periods_per_year=periods_per_year), 4),
        "max_drawdown_pct": mdd_pct,
        "calmar": round(calmar(cagr_pct, mdd_pct), 4),
        "omega": round(omega(returns), 4),
    }
