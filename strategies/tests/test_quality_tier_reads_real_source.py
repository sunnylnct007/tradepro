"""A cache hit is not a provenance claim.

16 Aug 2026: the Data screen reported **"0 from IBKR, 172 from the yfinance
fallback"** for 1-minute bars. That was false. `_quality_tier` graded
`provider_used == "cache"` as bronze outright, and on a steady-state run almost
every symbol IS a cache hit — so the G/S/B summary said 0 gold, and the
readiness endpoint that parses that summary turned it into a statement about
where the data came from.

Ground truth on disk at the time: 5m bars were 47,020 ibkr_web against 140,712
yfinance — a quarter IBKR, not zero.

"Which provider answered THIS call" is a different question from "where did
these bars come from". Only the stored `source` column knows the second one.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradepro_strategies.cli.bar_cache_harvest import _quality_tier


def _df(sources: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"close": [1.0] * len(sources), "source": sources})


class TestFreshFetches:
    def test_ibkr_web_complete_is_gold(self):
        assert _quality_tier("ibkr_web_ok", True) == "gold"

    def test_ibkr_web_partial_is_silver(self):
        assert _quality_tier("ibkr_web_ok", False) == "silver"

    def test_yfinance_is_bronze(self):
        assert _quality_tier("yfinance_ok", True) == "bronze"

    def test_nothing_served_is_missing(self):
        assert _quality_tier(None, True) == "missing"
        assert _quality_tier("none", True) == "missing"


class TestCacheHitsGradeOnTheStoredSource:
    def test_cached_ibkr_bars_are_GOLD_not_bronze(self):
        """THE regression: this is the case that made the screen say 0 IBKR."""
        assert _quality_tier("cache", True, _df(["ibkr_web"] * 100)) == "gold"

    def test_cached_yfinance_bars_stay_bronze(self):
        assert _quality_tier("cache", True, _df(["yfinance"] * 100)) == "bronze"

    def test_cached_ibkr_but_incomplete_is_silver(self):
        assert _quality_tier("cache", False, _df(["ibkr_web"] * 100)) == "silver"

    def test_majority_ibkr_grades_gold(self):
        assert _quality_tier("cache", True, _df(["ibkr_web"] * 60 + ["yfinance"] * 40)) == "gold"

    def test_majority_yfinance_grades_bronze(self):
        assert _quality_tier("cache", True, _df(["ibkr_web"] * 40 + ["yfinance"] * 60)) == "bronze"

    def test_exactly_half_ibkr_grades_gold(self):
        # Tie goes to golden — documented, so the boundary isn't accidental.
        assert _quality_tier("cache", True, _df(["ibkr_web"] * 50 + ["yfinance"] * 50)) == "gold"

    def test_gateway_ibkr_also_counts_as_golden(self):
        assert _quality_tier("cache", True, _df(["ibkr"] * 10)) == "gold"

    def test_no_frame_falls_back_to_bronze_not_gold(self):
        """Unknown provenance must never be PROMOTED — the standing rule."""
        assert _quality_tier("cache", True, None) == "bronze"

    def test_empty_frame_falls_back_to_bronze(self):
        assert _quality_tier("cache", True, pd.DataFrame()) == "bronze"

    def test_frame_without_a_source_column_falls_back_to_bronze(self):
        assert _quality_tier("cache", True, pd.DataFrame({"close": [1.0]})) == "bronze"


class TestFreshFetchIgnoresTheFrame:
    def test_a_live_ibkr_fetch_is_gold_regardless_of_old_cached_rows(self):
        """provider_used already names the answer for a fresh fetch; the frame
        may still contain older yfinance history and must not demote it."""
        assert _quality_tier("ibkr_web_ok", True, _df(["yfinance"] * 100)) == "gold"
