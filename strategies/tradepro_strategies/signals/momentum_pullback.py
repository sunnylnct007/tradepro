"""Momentum pullback — the rule graded in MOMENTUM_GATES_V2.md, as a signal
module with the same interface as `mean_reversion`.

    5,815 trades · 47.0% win · +1.53%/trade · 34-bar median hold · worst -14.7%
    ALL SIX GATES PASS (variant C: pullback entry + hard -8% stop)

WHY THIS FILE EXISTS. The rule was gated in August and has never placed a
single order. It carries a GATED badge earned entirely on backtest, while the
swing sleeve's badge is backed by both a backtest AND live fills. Owner, 24 Sep
2026: *"unless we start booking these trades how will we know if our strategy
is really working or not"*. That is the whole argument — a backtest is a claim,
a fill is evidence.

THE ENTRY IS IMPORTED, NOT RETYPED. `_entry_signal` already exists in
cli/momentum_candidates.py and is the single definition used by BOTH the live
screen and its per-symbol replay. Copying it here would create a third copy of
a rule this desk has already been bitten by duplicating — the screen and the
paper lane would drift and nothing would say so. So this module imports it and
adds only what the paper engine needs that a screen does not: an exit.

WHAT DIFFERS FROM MEAN REVERSION, and why the interface still fits:
  * NO TARGET. Mean reversion exits AT the 20-day mean and can name the level.
    Momentum runs until it gives back 8% from its peak, so `target_price`
    returns None and the engine renders "none (trails)" rather than inventing
    a number the rule never computes.
  * A TRAILING STOP needs the peak since entry. `exit_decision` receives
    `bars_held`, so the entry index is i - bars_held and the peak is available
    from the same closes the signature already carries. No new plumbing.
"""
from __future__ import annotations

# The ONE definition of this entry, shared with the screen. Never retyped.
from ..cli.momentum_candidates import (  # noqa: F401
    MAX_HOLD, STOP_PCT, TRAIL_PCT, _entry_signal, sma,
)

#: Bars the entry itself needs (the rule reads a 200-SMA and starts at 210),
#: plus headroom, matching how the study indexed.
MIN_BARS = 210 + 10

#: Trailing-stop distance from the peak since entry. Same 8% as the hard stop
#: — variant C of the gated study, not a value chosen here.
TRAIL_FROM_PEAK = TRAIL_PCT

TAG = "momentum pullback entry"


def entry_signal(closes: list[float], i: int) -> bool:
    """Pullback to the 10-day average inside an established uptrend.

    Delegates to the screen's own definition so the two can never disagree.
    Highs and lows are unused by that rule; it is a close-only condition, and
    passing closes for all three is what the study itself did.
    """
    return bool(_entry_signal(closes, closes, closes, i))


def target_price(closes: list[float], i: int) -> None:
    """NO FIXED TARGET. The rule rides the trend until the trail is hit.

    Returning None rather than a large number is deliberate: a sentinel like
    inf would be printed, compared and eventually believed. None forces every
    caller to handle "this rule has no target" explicitly.
    """
    return None


def stop_price(fill_price: float) -> float:
    """The HARD stop only. The trailing stop depends on the peak since entry
    and therefore cannot be computed from a fill price alone — it lives in
    `exit_decision`, which has the history."""
    return fill_price * (1 - STOP_PCT)


def trail_price(closes: list[float], i: int, *, bars_held: int) -> float | None:
    """Where the trailing stop sits on bar i, from the peak since entry."""
    start = i - max(bars_held, 0)
    if start < 0 or start > i:
        return None
    peak = max(closes[start:i + 1])
    return peak * (1 - TRAIL_FROM_PEAK)


def exit_decision(closes: list[float], i: int, *, fill_price: float,
                  bars_held: int) -> tuple[bool, str] | tuple[bool, None]:
    """Should an open position close on bar i? Returns (exit, reason).

    Checked on the CLOSE, which is what the study did. A stop checked on the
    close does not survive a gap, and the live sleeve must not pretend
    otherwise — the study's worst trade is -14.7% against an -8% stop for
    exactly that reason.

    Order matters and matches the study: hard stop first, then the trail, then
    the timeout.
    """
    if closes[i] <= stop_price(fill_price):
        return True, "stop"
    trail = trail_price(closes, i, bars_held=bars_held)
    if trail is not None and closes[i] <= trail:
        return True, "trail"
    if bars_held >= MAX_HOLD:
        return True, "timeout"
    return False, None


def reward_risk(closes: list[float], i: int) -> float:
    """Ranking key when more candidates fire than the position cap allows.

    Mean reversion ranks by distance-to-target over a fixed stop, which this
    rule has no target for. It ranks instead by how far the close sits above
    its own 200-day average: the study's entry is a pullback inside an uptrend,
    and a deeper-established trend is the closest thing the rule has to
    conviction.

    NOT MEASURED AS A RANKING. The gates graded the rule taking every signal;
    they say nothing about which to prefer under a cap. Stated here so nobody
    later reads this as a tested edge.
    """
    s200 = sma(closes, i, 200)
    if s200 <= 0:
        return 0.0
    return float(closes[i] / s200 - 1.0)


# THE WORDS MUST DESCRIBE THE TEST, NOT A STORY ABOUT IT (25 Sep 2026).
#
# Both sites said "pullback to the 10-day avg". The rule admits
# close <= 10-day * 1.005 — up to half a percent ABOVE the average — and only
# requires that yesterday's close was above yesterday's average. Measured on
# the live board the day momentum went live:
#
#     AAPL   337.02 -> 335.92   -0.33%   +0.77% -> +0.16% vs its 10-day
#     TECH    72.52 ->  72.57   +0.07%   +0.24% -> +0.24% vs its 10-day
#
# AAPL did pull back, slightly. TECH DID NOT: the price ROSE and its distance
# to the average is unchanged. It qualified by sitting near its 10-day two days
# running. The row claimed a move the price never made, and a reader checking
# the chart would have found the board wrong rather than the rule.
#
# Two readers also mistook the GATED badge for "blocked" within a minute of
# each other; when two readers make the same error the label is wrong, not the
# readers. Same defect class, same fix: say what is true.
def entry_reason(closes: list[float], i: int) -> str:
    s10, s20, s200 = sma(closes, i, 10), sma(closes, i, 20), sma(closes, i, 200)
    return (f"back at its 10-day average in an uptrend. close {closes[i]:.2f} "
            f"(10d {s10:.2f}, 20d {s20:.2f}, "
            f"{100 * (closes[i] / s200 - 1):.1f}% over the 200-day), "
            f"hard stop {stop_price(closes[i]):.2f} (-{100 * STOP_PCT:.0f}%), "
            f"then trails {100 * TRAIL_FROM_PEAK:.0f}% from the peak, "
            f"timeout {MAX_HOLD} sessions")
