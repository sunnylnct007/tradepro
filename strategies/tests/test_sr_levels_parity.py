"""Parity + causality pins for the S/R level port.

SR_LEVEL_STUDY_GATES_V1.md grades THE LINES ACTUALLY ON THE DESK, so the Python
must reproduce `CandleIchimokuChart.tsx::pivotLevels` exactly — including the
parts that are arguably not ideal (strict comparison, divide-by-incoming in the
cluster test, no volume, no recency weighting). Improvements would silently
change what is being graded.

The causality tests matter most: a swing high at bar i is unknowable until bar
i+win, and using the full series to find pivots then "testing" reactions to
them is look-ahead — the single most likely way to manufacture a spurious edge
in this study.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.quant_engine.sr_levels import (
    CLUSTER_PCT, MAX_SCAN, WIN, _collapse, levels_asof, pivot_levels,
)


class TestShippedConstants:
    def test_constants_match_the_chart(self):
        # If these drift from the TSX the study stops grading what ships.
        assert (WIN, CLUSTER_PCT, MAX_SCAN) == (5, 0.005, 240)


class TestPivotDetection:
    def test_single_clear_peak_and_trough(self):
        highs = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1]
        lows = [10, 9, 8, 7, 6, 1, 6, 7, 8, 9, 10]
        hi, lo = pivot_levels(highs, lows)
        assert [l.level for l in hi] == [10]
        assert [l.level for l in lo] == [1]

    def test_the_final_win_bars_can_never_form_a_pivot(self):
        """The TS loop bound is `i < len - win`; that IS the confirmation lag."""
        highs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 99]   # 99 is the last bar
        lows = [1] * 11
        hi, _ = pivot_levels(highs, lows)
        assert 99 not in [l.level for l in hi]

    def test_too_short_a_series_yields_nothing(self):
        hi, lo = pivot_levels([1, 2, 3], [1, 2, 3])
        assert hi == [] and lo == []

    def test_strict_comparison_keeps_a_flat_double_top(self):
        """Ported deliberately: `>` not `>=`, so equal highs both qualify."""
        highs = [1, 2, 3, 4, 5, 9, 9, 4, 3, 2, 1, 1, 1]
        lows = [1] * 13
        hi, _ = pivot_levels(highs, lows)
        assert any(abs(l.level - 9) < 1e-9 for l in hi)

    def test_a_zero_price_cannot_crash_the_port(self):
        """JS yields NaN/Infinity here and falls through; Python would raise.
        Prices are never 0, but the port must not DIVERGE from the chart."""
        assert _collapse([0.0, 0.0], CLUSTER_PCT)[0].touches == 2
        assert len(_collapse([0.0, 50.0], CLUSTER_PCT)) == 2


class TestClustering:
    def test_near_equal_pivots_merge_to_running_mean(self):
        out = _collapse([100.0, 100.2, 105.0], CLUSTER_PCT)
        assert len(out) == 2
        assert out[0].touches == 2
        assert out[0].level == pytest.approx(100.1)
        assert out[1].level == pytest.approx(105.0)
        assert out[1].touches == 1

    def test_distinct_shelves_stay_separate(self):
        """The 0.5% width exists so 27.35 and 27.62 do NOT merge (the WBD case
        named in the shipped comment)."""
        out = _collapse([27.35, 27.62], CLUSTER_PCT)
        assert len(out) == 2

    def test_touch_count_is_what_the_chart_labels_Rx3(self):
        out = _collapse([50.0, 50.1, 50.2], CLUSTER_PCT)
        assert len(out) == 1 and out[0].touches == 3

    def test_empty_input(self):
        assert _collapse([], CLUSTER_PCT) == []


class TestCausality:
    """The gates file calls this out as the most likely source of a fake edge."""

    def _series(self):
        highs = [10, 11, 12, 13, 14, 30, 14, 13, 12, 11, 10,
                 11, 12, 13, 14, 40, 14, 13, 12, 11, 10]
        lows = [1] * len(highs)
        return highs, lows

    def test_levels_asof_cannot_see_a_future_pivot(self):
        highs, lows = self._series()
        # The 40-peak sits at index 15 and is confirmed only at index 20.
        hi, _ = levels_asof(highs, lows, t=14)
        assert 40 not in [l.level for l in hi], "used a pivot from the future"

    def test_the_earlier_pivot_is_visible_once_confirmed(self):
        highs, lows = self._series()
        # The 30-peak sits at index 5, confirmed at index 10.
        hi, _ = levels_asof(highs, lows, t=10)
        assert any(abs(l.level - 30) < 1e-9 for l in hi)

    def test_a_pivot_is_not_visible_before_its_confirmation_bar(self):
        highs, lows = self._series()
        hi, _ = levels_asof(highs, lows, t=9)
        assert 30 not in [l.level for l in hi], "pivot used before confirmation"

    def test_asof_is_a_prefix_of_the_full_series_result(self):
        """Sanity: asof(T) must equal running the shipped function on the
        first T+1 bars — nothing more, nothing less."""
        highs, lows = self._series()
        for t in (10, 14, 20):
            assert levels_asof(highs, lows, t) == pivot_levels(highs[: t + 1], lows[: t + 1])


class TestScanWindow:
    def test_only_the_last_max_scan_bars_are_considered(self):
        # An enormous pivot far outside the window must not appear.
        highs = [999] + [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1] * 40
        lows = [1] * len(highs)
        hi, _ = pivot_levels(highs, lows, max_scan=50)
        assert 999 not in [l.level for l in hi]
