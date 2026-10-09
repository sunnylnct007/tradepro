"""A zero-volume bar that MOVES the price is corruption, not thin trading.

THE MISS, 9 Oct 2026. WBD carried two fabricated bars:

    2026-10-05  c=  30.95  vol=93,241,676
    2026-10-06  c= 559.50  vol=0           <- an 18x move on no trades
    2026-10-07  c= 559.50  vol=0

poison_check scored only the SECOND one, because a phantom was defined as an
UNCHANGED close on zero volume. One phantom against a budget of four, so the
series passed and the signal replay reported +$50,742 of fictional profit on
a position nobody holds.

Worse, supplying volumes made the check WEAKER: without them the old ratio
test caught WBD at 20.6x. More information must never remove a detection.

WHY NOT JUST USE THE RATIO. Because it is not a corruption test, it is a
growth test. Measured across the live 956-name universe it rejects MU (6.3x),
LITE (6.9x), WDC and VICR — real stocks that really did multiply. That is the
cry-wolf failure this project has made before, and nearly made again here.
The honest discriminator is "no volume yet the price moved".
"""
from __future__ import annotations

from tradepro_strategies.universe import (
    MAX_PHANTOM_BARS, ZERO_VOL_JUMP, poison_check,
)


def _series(n=200, px=100.0):
    return [px] * n, [1_000_000.0] * n


def test_the_WBD_bar_is_rejected():
    """The exact shape that slipped through: one jumped bar, zero volume."""
    c, v = _series()
    c += [559.50, 559.50]
    v += [0.0, 0.0]
    ok, why = poison_check(c, v)
    assert not ok, "an 18x move on zero volume passed as clean"
    assert why >= 1, "the phantom count should include the jumped bar"


def test_a_single_jumped_bar_is_enough():
    """There is no benign reading, so there is no budget for it."""
    c, v = _series()
    c += [140.0]          # +40% on nothing
    v += [0.0]
    assert not poison_check(c, v)[0]


def test_real_growth_on_real_volume_is_KEPT():
    """MU went 100 -> 1074 on genuine volume. A ratio test would reject it."""
    c = [100.0 + i * 5 for i in range(200)]      # ends ~10x the start
    v = [1_000_000.0] * 200
    ok, _ = poison_check(c, v)
    assert ok, "a stock that genuinely multiplied was rejected as corrupt"


def test_repeated_zero_volume_bars_keep_their_budget():
    """A young ETF genuinely does not trade some days — that is history."""
    c, v = _series()
    for _ in range(MAX_PHANTOM_BARS):
        c.append(c[-1])      # unchanged close
        v.append(0.0)
    assert poison_check(c, v)[0]


def test_too_many_repeated_phantoms_still_fail():
    c, v = _series()
    for _ in range(MAX_PHANTOM_BARS + 2):
        c.append(c[-1])
        v.append(0.0)
    assert not poison_check(c, v)[0]


def test_a_small_move_on_zero_volume_is_tolerated():
    """Rounding or a stale print, not an 18x fabrication."""
    c, v = _series()
    c += [100.0 * (1 + ZERO_VOL_JUMP / 2)]
    v += [0.0]
    assert poison_check(c, v)[0]


def test_supplying_volume_never_weakens_the_verdict():
    """The original bug in one line: volumes made WBD look clean."""
    c, v = _series()
    c += [3000.0, 3000.0]      # 30x: trips the no-volume ratio path too
    v += [0.0, 0.0]
    with_vol = poison_check(c, v)[0]
    without = poison_check(c)[0]
    assert not without, "the no-volume path should already reject this"
    assert not with_vol, (
        "passing volumes turned a REJECT into a PASS — more information must "
        "never remove a detection")


def test_an_empty_series_is_not_a_failure():
    assert poison_check([], [])[0]
