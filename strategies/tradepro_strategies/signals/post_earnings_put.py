"""Post-earnings cash-secured put — THE rule, in one place.

The screen, any backtest and any live daemon import from here. Nothing
re-implements it. This module exists because the repo's dominant bug is the
same knowledge written twice and then one copy changing: the swing screen once
advertised a 10-session exit while the strategy held 20, and a backtest harness
graded the old rule for a week.

EVIDENCE (POST_EARNINGS_PUT_GATES_V1.md, gates committed BEFORE each run):

    V1, no market filter   n=284  win 87.0%  mean +1.19%  p5 -5.48%  worst -54.05%
                           FAILED V0 (284<300) and G5 (halves inconsistent)

    V2, SPY > 200-SMA      n=229  win 89.5%  mean +1.29%  p5 -4.72%  worst -23.40%
                           ALL EIGHT GATES PASS
    same-filtered null     n=32617          mean -0.15%

The null is the number that matters: selling 10%-OTM puts indiscriminately in
this universe earns NOTHING after costs. The edge is in the trigger.

HONEST LIMITS, stated here rather than discovered later:
  * Earnings history begins ~Oct 2020, so both graded halves sit in ONE
    post-2020 regime. This says nothing about a sustained bear market.
  * W6 ("2022 must not lose") passed on NINE events — the filter removed 41 of
    2022's 50 qualifying drops. "2022 is fixed" is weakly evidenced.
  * Worst single trade after filtering and vol-sizing is still -23.4%.
  * Verdict on record: PAPER FORWARD TEST at small size. Not funded.
"""
from __future__ import annotations

# ── The rule ────────────────────────────────────────────────────────────────
DROP_PCT = -0.08        # the report-day move that defines "corrected"
OTM_PCT = 0.10          # strike this far below the post-drop close
DTE_TARGET = 30         # calendar-ish sessions to expiry
MAX_REPORT_AGE = 5      # sessions since the report; after that it is not this setup

# The 200-session trend window is IMPORTED, not retyped. It was written here as
# `TREND_WINDOW = 200` and the duplicate-constant guard caught it immediately —
# correctly, even though the two uses differ: mean_reversion applies it to the
# SYMBOL's own average, this applies it to SPY's. Same number, same concept,
# and two copies is exactly how MAX_HOLD outlived the change from 10 to 20.
# If the trend definition ever moves, it moves in one place.
from .mean_reversion import TREND_WINDOW  # noqa: E402  — SPY's SMA window

# ── Sizing (part of the strategy, not presentation — see the gates file) ────
TARGET_VOL = 0.35       # annualised anchor
SIZE_CAP = 2.0          # never more than 2x base


def qualifies(report_move: float | None) -> bool:
    """Did the stock 'correct' on its report? The single source of truth."""
    return report_move is not None and report_move <= DROP_PCT


def strike_for(spot: float) -> float:
    """Where the put goes. Rounded to a cent; real chains snap to their own
    increments, which is the screen's job, not the rule's."""
    return round(spot * (1.0 - OTM_PCT), 2)


def size_factor(annual_vol: float | None) -> float:
    """Vol-scaled collateral. The owner's choice: cap the tail by SIZE rather
    than by excluding names.

    A 70%-vol name gets half the collateral of a 35%-vol one. NOTE what the
    graded run showed: this does NOT contain the tail in a drawdown, because
    everything is volatile together, so scaling by a symbol's own vol scales
    nothing relative to the market. That is what the SPY filter is for.
    """
    if not annual_vol or annual_vol <= 0:
        return 0.0
    return min(SIZE_CAP, TARGET_VOL / annual_vol)


def market_ok(spy_close: float | None, spy_sma200: float | None) -> bool | None:
    """The V2 gate. None means "cannot tell" — the caller must BLOCK on that
    rather than assume a healthy market, which is the whole point of the gate.
    """
    if spy_close is None or spy_sma200 is None or spy_sma200 <= 0:
        return None
    return spy_close > spy_sma200
