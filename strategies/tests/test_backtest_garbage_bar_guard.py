"""Garbage-bar guard (backtest.py _compute_stats): a real market crash must
NOT be flagged stats_suspect, but an isolated corrupt bar still must be.

Fixed 3 Aug 2026: the guard compared the single biggest daily move against
the FULL-HISTORY std, which over a decade-plus window is diluted so far by
calm periods that a real crash day (e.g. 2020-03-16, the worst day of the
COVID crash) reads as "statistically impossible" — live-verified this was
flagging ~100% of symbols with pre-2021 history, which is why the Decide
screen showed 0 BUY across every universe two days running. Now compares
against a LOCAL (60-trading-day, excluding the flagged bar) std instead.
"""
import numpy as np
import pandas as pd

from tradepro_strategies.backtest import _compute_stats


def _equity_from_returns(returns: list[float], start: float = 100.0) -> pd.Series:
    idx = pd.date_range("2015-01-01", periods=len(returns) + 1, freq="D")
    vals = [start]
    for r in returns:
        vals.append(vals[-1] * (1 + r))
    return pd.Series(vals, index=idx)


def test_real_crash_cluster_not_flagged_suspect():
    """A real crash: several volatile days clustered together (like COVID
    Feb-Mar 2020), including one very large single-day move. Must NOT be
    flagged — this is the exact false-positive class the fix targets."""
    rng = np.random.default_rng(42)
    calm = list(rng.normal(0.0003, 0.01, 500))
    # Volatility clusters around the crash: several elevated-vol days, one
    # of them a genuine outsized move (~-13%, matching 2020-03-16).
    crash_cluster = [-0.05, 0.03, -0.08, -0.13, 0.09, -0.06, 0.07, -0.04, 0.05, -0.03]
    recovery = list(rng.normal(0.0008, 0.015, 300))
    returns = calm + crash_cluster + recovery
    equity = _equity_from_returns(returns)
    stats = _compute_stats(equity, 100.0)
    assert stats["stats_suspect"] is False, stats.get("stats_suspect_reason")


def test_isolated_corrupt_bar_still_flagged_suspect():
    """One huge isolated move in an otherwise calm series (no surrounding
    volatility) — a bad print, not a real event. Must still be flagged."""
    rng = np.random.default_rng(7)
    calm_before = list(rng.normal(0.0002, 0.008, 400))
    bad_print = [0.40]  # a 40% single-day "move" with calm neighbours
    calm_after = list(rng.normal(0.0002, 0.008, 400))
    returns = calm_before + bad_print + calm_after
    equity = _equity_from_returns(returns)
    stats = _compute_stats(equity, 100.0)
    assert stats["stats_suspect"] is True
    assert "outlier bar" in (stats["stats_suspect_reason"] or "")


def test_calm_series_not_flagged():
    """A boring, calm series with no crash and no corruption — the common
    case — must never be flagged."""
    rng = np.random.default_rng(1)
    returns = list(rng.normal(0.0003, 0.008, 800))
    equity = _equity_from_returns(returns)
    stats = _compute_stats(equity, 100.0)
    assert stats["stats_suspect"] is False
