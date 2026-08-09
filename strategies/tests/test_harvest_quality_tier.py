"""Regression tests for the 2026-08-09 harvest-display grading bug and the
ibkr_web provider's retry-aware timeout.

Bug: ``_quality_tier`` compared the store's chain outcome (``"ibkr_web_ok"``,
``"ibkr_ok"`` — always suffixed) against the bare string ``"ibkr"``, so every
IBKR-sourced fetch printed as 🥉 bronze and the run's G/S/B summary
under-reported IBKR success — masking exactly the "is ibkr_web working?"
question this pipeline is supposed to answer.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradepro_strategies.cli.bar_cache_harvest import _quality_tier
from tradepro_strategies.bar_cache.providers.ibkr_web_provider import IBKRWebProvider


class TestQualityTier:
    @pytest.mark.parametrize("provider_used", ["ibkr_ok", "ibkr_web_ok", "IBKR_WEB_OK"])
    def test_ibkr_sources_complete_are_gold(self, provider_used):
        assert _quality_tier(provider_used, complete=True) == "gold"

    @pytest.mark.parametrize("provider_used", ["ibkr_ok", "ibkr_web_ok"])
    def test_ibkr_sources_incomplete_are_silver(self, provider_used):
        assert _quality_tier(provider_used, complete=False) == "silver"

    @pytest.mark.parametrize("provider_used", ["yfinance_ok", "ig_ok", "cache"])
    def test_non_ibkr_sources_stay_bronze(self, provider_used):
        assert _quality_tier(provider_used, complete=True) == "bronze"

    @pytest.mark.parametrize("provider_used", [None, "", "none"])
    def test_missing_provider_is_missing(self, provider_used):
        assert _quality_tier(provider_used, complete=True) == "missing"


class TestIBKRWebProviderTimeout:
    def test_timeout_covers_backend_retry_window(self):
        """The backend retries IBKR transients in-request (worst ~35s); the
        provider's HTTP timeout must exceed that or a recoverable fetch gets
        aborted mid-retry and falls through to yfinance/BRONZE."""
        seen = {}

        def fake_get(url, headers, timeout):
            seen["timeout"] = timeout
            # Minimal valid payload: one bar inside the window.
            return 200, {"bars": [{"t": 1754870400000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
                         "conid": 1}

        provider = IBKRWebProvider(base="http://test", token=None, _get=fake_get)
        df, meta = provider.fetch(
            canonical="AAPL", asset_class="us_etf", resolution="1d",
            start=pd.Timestamp("2025-08-01").to_pydatetime(),
            end=pd.Timestamp("2026-08-12").to_pydatetime(),
        )
        assert not df.empty
        assert seen["timeout"] >= 45, (
            f"provider timeout {seen['timeout']}s won't survive the backend's "
            f"~35s worst-case IBKR retry window"
        )
