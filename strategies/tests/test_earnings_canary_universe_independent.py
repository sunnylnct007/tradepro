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


# ── Late-bounce guard (UBER, 12 Aug 2026) — lives here to reuse the
# market_state import; the bounce-zone BUY must not fire on a name at the
# top of its recent range that already ran off the low.
def test_late_bounce_is_wait_not_buy():
    """The late-bounce guard must ACTUALLY EXECUTE, not merely produce a
    non-BUY verdict some earlier gate already supplied.

    The original version of this test asserted only `!= "BUY"` on a series
    that sat BELOW its 200-SMA, so it exited at the trend-coherence guard and
    never reached the new code — which is how RANGE13_LATE_BOUNCE_PCTILE
    shipped undefined and raised NameError inside market_state() for two
    days, failing 11 of 14 comparator universes on every cycle. This version
    pins the branch by asserting the reason it emits.
    """
    import numpy as np
    import pandas as pd
    from tradepro_strategies.market_state import market_state

    rng = np.random.default_rng(4)
    seg1 = np.linspace(70, 100, 150)     # run-up to the 52w high
    seg2 = np.linspace(100, 74, 55)      # the fall
    seg3 = np.linspace(74, 88, 55) + rng.normal(0, 0.9, 55)   # choppy recovery
    close = np.concatenate([seg1, seg2, seg3])
    idx = pd.bdate_range(end="2026-08-14", periods=len(close))
    df = pd.DataFrame({"adj_close": close, "high": close * 1.01,
                       "low": close * 0.99, "close": close}, index=idx)
    st = market_state("LATEB", df)

    # preconditions that route execution INTO the late-bounce branch
    assert st.above_sma_200 is True
    assert st.pct_off_52w_high_pct >= 8.0
    assert 30 < st.rsi_14 < 70
    assert st.range_position_13w_pct >= 85 or st.bounce_from_13w_low_pct >= 15

    assert st.entry_signal == "WAIT"
    assert "LATE BOUNCE" in st.entry_reason, st.entry_reason
    assert "off the recent low" in st.entry_reason


def test_late_bounce_constants_are_defined():
    """Regression for the NameError outage: the guard's thresholds must exist
    as module constants, not just be referenced."""
    from tradepro_strategies import market_state as ms
    assert isinstance(ms.RANGE13_LATE_BOUNCE_PCTILE, float)
    assert isinstance(ms.BOUNCE_ALREADY_RUN_PCT, float)
