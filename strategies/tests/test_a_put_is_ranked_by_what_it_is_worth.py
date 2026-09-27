"""Delta and yield both put the WORST trade at the top of the board.

THE CASE THAT PROVES IT (24 Sep 2026). Ranked by the keep-probability that
delta implies, ORCL led the board at 80.1% — and ORCL was the only NEGATIVE
expectancy row on it. When ORCL breaches it goes a median 10% past the strike.

Rare-but-deep beats frequent-but-shallow, and no probability-of-assignment
number can see that, because DEPTH is not in it. Yield cannot see it either:
ORCL paid 30.4%/yr, comfortably mid-board.

    EV = premium collected − P(breach) × depth when breached − spread

P(breach) and depth come from the NAME'S OWN history over a window this long,
not from a model. The model's N(−d2) is still published beside it, and where
the two disagree is exactly where the ranking changes.
"""
from __future__ import annotations

from tradepro_strategies.cli.options_screen import SPREAD_FRAC, expectancy_pct


def _ev(ann, dte, breach, depth):
    r = expectancy_pct(annualized_yield_pct=ann, dte=dte,
                       breach_pct=breach, median_breach_depth_pct=depth)
    return None if r is None else r["expectancy_pct"]


def test_the_highest_keep_probability_row_can_be_the_worst_trade():
    """ORCL vs DELL, both from the live board on 24 Sep.

    ORCL: 80.1% keep-probability (the best on the board), 30.4%/yr.
    DELL: 73.9% keep-probability, 37.5%/yr.

    By delta ORCL wins. By expectancy it is the only loser."""
    orcl = _ev(30.4, 36, 33.3, 10.0)
    dell = _ev(37.5, 36, 11.2, 2.7)
    assert orcl is not None and dell is not None
    assert orcl < 0, f"ORCL should be negative-expectancy, got {orcl:+.2f}%"
    assert dell > 0 and dell > orcl, (
        f"DELL {dell:+.2f}% must rank above ORCL {orcl:+.2f}% — ranking by "
        "keep-probability reversed them")


def test_a_fat_yield_does_not_survive_a_deep_tail():
    """MRVL paid the fattest yield on the board at 55.2%/yr. Its breach depth
    is 10.9%, the worst of the eligible rows. It still clears — but by far less
    than the yield suggests, and that gap is the whole point."""
    mrvl = _ev(55.2, 22, 11.2, 10.9)
    gm = _ev(27.9, 22, 11.6, 1.6)
    assert mrvl is not None and gm is not None
    assert mrvl > 0
    # MRVL's yield is 2x GM's; its expectancy advantage is nothing like 2x.
    assert mrvl / gm < 2.0, (
        f"MRVL {mrvl:+.2f}% vs GM {gm:+.2f}% — a 2x yield must not read as a "
        "2x trade once the tail is priced")


def test_the_spread_is_deducted_and_is_the_measured_one():
    """8.9% of mid, measured, not assumed. A thin edge must not survive it."""
    assert abs(SPREAD_FRAC - 0.089) < 1e-9
    r = expectancy_pct(annualized_yield_pct=10.0, dte=30,
                       breach_pct=0.0, median_breach_depth_pct=0.0)
    assert r is not None
    prem = 10.0 * 30 / 365
    # published values are rounded to 3dp — compare at that precision,
    # not tighter than the number is stated
    assert abs(r["spread_cost_pct"] - prem * 0.089) < 5e-4
    assert r["expectancy_pct"] < prem, "the spread must reduce the expectancy"


def test_a_missing_input_yields_NO_NUMBER_rather_than_a_guess():
    """An expectancy computed from a missing term means nothing, and this desk
    does not publish those. Absence must stay absence."""
    assert _ev(30.0, 30, None, 2.0) is None, "no breach history -> no expectancy"
    assert _ev(30.0, 30, 10.0, None) is None, "no depth -> no expectancy"
    assert _ev(None, 30, 10.0, 2.0) is None, "no yield -> no expectancy"
    assert _ev(30.0, None, 10.0, 2.0) is None, "no DTE -> no expectancy"


def test_the_formula_states_its_own_arithmetic():
    """Every number on this desk shows its working."""
    r = expectancy_pct(annualized_yield_pct=37.5, dte=36,
                       breach_pct=11.2, median_breach_depth_pct=2.7)
    assert r is not None
    for part in ("premium", "assignment", "spread"):
        assert part in r["formula"], f"the formula must name its {part} term"
    assert r["clears_the_spread"] is True
