"""Live-vs-backtest tracking — the "is the strategy proving itself?" instrument.

The clone is configured to be the backtested strategy (clean large_50, gate off,
8% stop, parity-locked). The only thing left is confirming the LIVE book tracks
the backtest expectation. This turns "eyeball it" into an honest verdict.

Deliberately conservative: a few days of live P&L is mostly noise (daily vol >>
the expected daily drift), so it refuses to judge until enough sessions have
passed (TOO_EARLY) rather than crying success/failure on noise — the
no-false-positives rule applied to the track record itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackingVerdict:
    status: str              # TOO_EARLY | ON_TRACK | AHEAD | BEHIND | DRAWDOWN_BREACH
    elapsed_days: int
    actual_return_pct: float        # live realised+open ÷ capital
    expected_return_pct: float      # backtest CAGR pro-rated to elapsed
    drawdown_bound_pct: float       # backtest max DD (negative) — the line not to cross
    note: str


def assess_tracking(
    actual_return_pct: float,
    elapsed_days: int,
    *,
    expected_cagr_pct: float,
    drawdown_bound_pct: float,
    min_days: int = 20,
    band_pct: float = 6.0,
) -> TrackingVerdict:
    """Compare live return to the backtest's pro-rated expectation.

    `band_pct` is the +/- tolerance (in percentage points of return) around the
    expected pro-rated return before we call it AHEAD/BEHIND — wide on purpose,
    because short windows are noisy. A live drawdown worse than the backtest's
    own max DD is the one hard flag (DRAWDOWN_BREACH) — that says the live risk
    profile is NOT what the backtest promised.
    """
    years = max(elapsed_days, 0) / 365.0
    expected = ((1 + expected_cagr_pct / 100.0) ** years - 1) * 100.0 if years > 0 else 0.0

    # The drawdown bound is breached regardless of how early it is — that's a
    # risk-profile failure, not a return question.
    if actual_return_pct <= drawdown_bound_pct:
        return TrackingVerdict(
            "DRAWDOWN_BREACH", elapsed_days, actual_return_pct, expected, drawdown_bound_pct,
            f"live {actual_return_pct:+.1f}% breached the backtest max-DD bound "
            f"{drawdown_bound_pct:.1f}% — live risk ≠ backtested risk; investigate.")

    if elapsed_days < min_days:
        return TrackingVerdict(
            "TOO_EARLY", elapsed_days, actual_return_pct, expected, drawdown_bound_pct,
            f"only {elapsed_days}d of clean-config history (<{min_days}d) — too noisy to "
            f"judge return tracking yet; no verdict (no false positives).")

    if actual_return_pct > expected + band_pct:
        status, msg = "AHEAD", "live ahead of the backtest pace"
    elif actual_return_pct < expected - band_pct:
        status, msg = "BEHIND", "live behind the backtest pace — watch for divergence"
    else:
        status, msg = "ON_TRACK", "live within tolerance of the backtest expectation"
    return TrackingVerdict(
        status, elapsed_days, actual_return_pct, expected, drawdown_bound_pct,
        f"{msg}: actual {actual_return_pct:+.1f}% vs expected {expected:+.1f}% "
        f"(±{band_pct:.0f}pp) over {elapsed_days}d.")


__all__ = ["TrackingVerdict", "assess_tracking"]
