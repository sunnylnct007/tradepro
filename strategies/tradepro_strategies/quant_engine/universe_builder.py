"""High-beta universe construction — port of UniverseBuilder.build_high_beta()
from the trader's reference strategy (docs/strategy.py).

The library function is intentionally **pure** (no network IO): caller
supplies the SPY close series and a dict of {ticker -> close series},
and the function returns the betas. This mirrors the existing
quant_engine convention (config.py / sleeve.py / vol_targeting.py are
all pure) so it composes cleanly into tests and CLI drivers without
either having to mock yfinance.

For end-to-end "scrape Wikipedia + fetch OHLC + ingest into Postgres
as a `high_beta` universe" use the companion CLI
``cli/build_high_beta_universe.py`` which wires this together with
``universes/wikipedia.py`` (the existing constituent scraper) and
``cache.py`` (the Parquet-backed OHLC cache).

Reference: QUANT_ENGINE_GAPS.md "Gap 2 — High-Beta Universe Builder".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BetaResult:
    """One row in the beta table — beta value + observation count."""
    ticker: str
    beta: float
    observations: int


def compute_beta(
    stock_returns: pd.Series,
    market_returns: pd.Series,
    min_obs: int,
) -> tuple[float, int]:
    """OLS beta of stock vs market over the common date intersection.

    Returns ``(beta, observations)``. ``beta`` is ``nan`` when there
    aren't enough overlapping observations or the market variance is
    zero (which would be a numerical fluke — e.g. holidays + a tiny
    test window). The trader's reference returns nan in the same
    cases; we surface the obs count too so callers can distinguish
    "we have data, beta really is below threshold" from "we have no
    data, fall back".
    """
    common = stock_returns.index.intersection(market_returns.index)
    obs = len(common)
    if obs < min_obs:
        return float("nan"), obs
    s = stock_returns.loc[common].to_numpy()
    m = market_returns.loc[common].to_numpy()
    var_m = float(np.var(m))
    if var_m <= 0.0:
        return float("nan"), obs
    cov = float(np.cov(s, m)[0, 1])
    return cov / var_m, obs


def build_high_beta(
    spy_close: pd.Series,
    candidate_closes: dict[str, pd.Series],
    *,
    min_beta: float = 1.5,
    beta_lookback: int = 252,
    crypto_exclude: frozenset[str] | set[str] | None = None,
) -> list[BetaResult]:
    """Filter ``candidate_closes`` to names with β > min_beta vs SPY.

    Parameters
    ----------
    spy_close : pd.Series
        SPY adjusted close, DatetimeIndex.
    candidate_closes : dict[str, pd.Series]
        Ticker -> adjusted close. Caller is responsible for ensuring
        each series has enough history (the trader's reference
        requires beta_lookback + 50 bars; we just enforce
        beta_lookback so the floor is configurable).
    min_beta : float
        Inclusion threshold. Default 1.5 matches the trader's config.
    beta_lookback : int
        Minimum number of overlapping return observations required to
        admit a candidate. Default 252 (one trading year) matches the
        trader's config.
    crypto_exclude : set[str] | None
        Tickers to drop before beta is computed — names that
        masquerade as equities but are really crypto-beta plays
        (COIN, MSTR, MARA, IBIT, etc.). Default None means honour the
        caller's universe as-is.

    Returns
    -------
    list[BetaResult]
        Names with β > min_beta, sorted highest-beta first. The
        exclusion + lookback fail-out are silent — callers wanting
        full audit detail should use ``compute_beta`` directly per
        ticker.
    """
    spy_returns = spy_close.pct_change().dropna()
    excluded = frozenset(crypto_exclude or ())
    keep: list[BetaResult] = []
    for ticker, close in candidate_closes.items():
        if ticker in excluded:
            continue
        stock_returns = close.pct_change().dropna()
        beta, obs = compute_beta(stock_returns, spy_returns, beta_lookback)
        if np.isnan(beta):
            continue
        if beta > min_beta:
            keep.append(BetaResult(ticker=ticker, beta=beta, observations=obs))
    keep.sort(key=lambda r: r.beta, reverse=True)
    return keep
