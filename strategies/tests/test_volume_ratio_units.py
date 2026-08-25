"""A volume RATIO is not immune to a units error that starts mid-window.

25 Aug 2026. IBKR reports 100-share lots and the conversion was applied at two
points in one pipeline, so stored volume is x100 in some months and not
others (data lane, 6c22ebd). A uniform error cancels in a ratio. A error that
begins partway through the window does not — and the 2026-08 partition is
inflated while 2026-07 is not.

Today the window sits mostly inside the inflated month, so `volume_vs_20d`
reads ~0.95-1.41: plausible, and wrong. The damage lands the other way round —
the first CORRECT bar entering a window that still holds inflated ones reads
0.011, and renders on the momentum screen as a 99% volume collapse on every
symbol at once.
"""
from __future__ import annotations

from tradepro_strategies.universe import volume_ratio


def test_clean_series_returns_a_ratio():
    v = [1_000 + 7 * i for i in range(40)]
    r, why = volume_ratio(v, 39)
    assert why is None and r is not None and 0.5 < r < 2.0


def test_a_genuine_volume_spike_is_not_mistaken_for_a_units_change():
    """The point of the guard is units, not activity. An 8x day is a real
    event and must still produce a number."""
    r, why = volume_ratio([1_000] * 39 + [8_000], 39)
    assert why is None
    assert r is not None and r > 5


def test_a_quiet_stretch_then_a_spike_still_reports():
    r, why = volume_ratio([1_000] * 30 + [500] * 9 + [6_000], 39)
    assert why is None and r is not None


def test_a_hundredfold_step_inside_the_window_withholds_the_ratio():
    """The real shape: four correct sessions, then the units change."""
    v = [1_000] * 20 + [95_000_000, 101_000_000, 112_000_000, 69_000_000]
    v += [6_000_000_000 + 10_000_000 * i for i in range(16)]
    r, why = volume_ratio(v, len(v) - 1)
    assert r is None
    assert why is not None and "units change" in why


def test_the_step_is_found_wherever_it_sits_not_assumed_at_the_midpoint():
    """Comparing first-half median to second-half median is the obvious test
    and it FAILS on the real case — only four of twenty bars precede the
    change, so both half-medians land on the inflated side."""
    v = [1_000] * 20 + [100] * 4 + [10_000] * 16       # step at position 4 of 20
    r, why = volume_ratio(v, len(v) - 1)
    assert r is None and "units change" in (why or "")


def test_a_step_too_close_to_the_edge_is_not_enough_evidence():
    """One or two odd bars at the boundary are not a units change; demanding
    at least three sessions each side keeps a single bad print from silencing
    a whole column."""
    v = [1_000] * 38 + [100_000, 1_000]
    r, why = volume_ratio(v, 39)
    assert r is not None and why is None


def test_missing_or_empty_volume_says_so_rather_than_dividing_by_zero():
    assert volume_ratio([], 5) == (None, "not enough history")
    assert volume_ratio([0] * 40, 39)[0] is None
