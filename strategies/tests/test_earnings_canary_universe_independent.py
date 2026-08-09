"""Regression coverage for the 2026-08-08 NVDA earnings-canary gap.

`_attach_bucket_and_rationale`'s degraded-feed canary (MA/V/AXP/JPM/MSFT/AAPL)
only checked rows already present in the current scan's universe — so a
narrow universe (e.g. us_growth_tech/us_semis, containing none of the six
canaries) got ZERO degraded-feed protection no matter how broken the earnings
feed was. NVDA's own EARNINGS_UNKNOWN then took the flat per-name 0.5x
penalty instead of escalating, even though NVDA's real earnings date (26 Aug
2026) was publicly known — the feed was broken, not the data genuinely
missing. Fixed by fetching canary dates independently of the scan universe.
"""
from __future__ import annotations

from unittest.mock import patch

from tradepro_strategies.compare import _attach_bucket_and_rationale


def _row(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "strategy": "ichimoku_equity",
        "rank": 1,
        "market_state": {"entry_signal": "BUY"},
        "earnings_signal": {},
    }


def test_dead_feed_detected_even_when_no_canary_is_in_universe():
    """The bug: a scan universe with zero canary symbols must still detect
    a fully-broken feed via the independent fetch, not silently skip it."""
    row = _row("NVDA")
    with patch("tradepro_strategies.earnings.fetch_earnings_in_range", return_value=[]), \
         patch("tradepro_strategies.earnings.fetch_upcoming_earnings", return_value=None), \
         patch("tradepro_strategies.news_sites.recent_earnings_mention", return_value=False):
        _attach_bucket_and_rationale([row], mean_threshold=-0.3, min_material=2,
                                      logger=None, llm_healthy=True)
    gate = row["earnings_gate"]
    assert gate["feed_degraded"] is True
    assert set(gate["dead_canaries"]) == {"MA", "V", "AXP", "JPM", "MSFT", "AAPL"}
    # Degraded-aware flag, not the old flat "penalize with no visibility" path.
    assert gate["flag"] in ("EARNINGS_UNVERIFIED", "EARNINGS_RECENT_NEWS")
    assert gate["rank_cap"] is True


def test_healthy_feed_does_not_falsely_flag_degraded():
    """Every canary WITH a real date must not trip the degraded-feed
    detector — the "no false positives" side of the same fix. The gate
    considers the feed degraded the moment even ONE canary is dead
    (bool(_dead_canaries)), so proving the true-negative path means every
    canary needs a live date."""
    def _fake_upcoming_all(symbol, api_base, **kw):
        return {"date": "2026-08-26"}

    row = _row("NVDA")
    with patch("tradepro_strategies.earnings.fetch_earnings_in_range", return_value=[]), \
         patch("tradepro_strategies.earnings.fetch_upcoming_earnings", side_effect=_fake_upcoming_all):
        _attach_bucket_and_rationale([row], mean_threshold=-0.3, min_material=2,
                                      logger=None, llm_healthy=True)
    gate = row["earnings_gate"]
    assert gate["feed_degraded"] is False
    assert gate["dead_canaries"] is None
    # NVDA itself still has no date in this test, so it stays plain UNKNOWN
    # (flat penalty) — correctly, since the FEED isn't degraded this time,
    # only NVDA's own row lacks a date.
    assert gate["flag"] == "EARNINGS_UNKNOWN"


def test_canary_already_in_universe_is_reused_not_refetched():
    """When a canary IS in-universe (MA here), use its existing row data —
    no refetch for THAT canary specifically (preserves the original, cheaper
    behaviour for the common case). The other five canaries aren't in this
    universe either, so they're still independently fetched — this test
    only pins that MA, specifically, isn't double-fetched."""
    ma_row = _row("MA")
    ma_row["earnings_signal"] = {"last_report_date": "2026-07-15"}
    nvda_row = _row("NVDA")

    def _fake_upcoming_all(symbol, api_base, **kw):
        return {"date": "2026-08-26"}

    with patch("tradepro_strategies.earnings.fetch_earnings_in_range", return_value=[]) as mock_hist, \
         patch("tradepro_strategies.earnings.fetch_upcoming_earnings", side_effect=_fake_upcoming_all) as mock_up:
        _attach_bucket_and_rationale([ma_row, nvda_row], mean_threshold=-0.3, min_material=2,
                                      logger=None, llm_healthy=True)
    fetched_symbols = {c.args[0] for c in mock_hist.call_args_list} | {c.args[0] for c in mock_up.call_args_list}
    assert "MA" not in fetched_symbols, "MA is in-universe with real data — must not be refetched"
    assert fetched_symbols == {"V", "AXP", "JPM", "MSFT", "AAPL"}
    assert nvda_row["earnings_gate"]["feed_degraded"] is False
