"""Run-level data-health verdict (9 Aug 2026, owner: 'if it's missing some
dataset we should make it loud and clear').

Key rule under test: MARKET CLOSED IS NOT AN EXCUSE — IBKR serves the last
session's snapshot off-hours, so dark fields are graded as DATA problems
regardless of market state; a closed market with complete data is healthy.
"""
from __future__ import annotations

from tradepro_strategies.cli.options_screen import screen_data_health


def _row(vega="bridge", chain="g3", premium=1.5):
    return {"vega_gate": vega, "chain_source": chain, "suggested_premium": premium}


class TestScreenDataHealth:
    def test_complete_data_closed_market_is_healthy(self):
        # Friday-close snapshot fully served → healthy even though closed.
        h = screen_data_health([_row() for _ in range(10)], market_open=False)
        assert h["degraded"] is False
        assert "Data healthy" in h["summary"]

    def test_widespread_iv_darkness_is_degraded_and_loud(self):
        rows = [_row(vega=None) for _ in range(5)] + [_row() for _ in range(5)]
        h = screen_data_health(rows, market_open=False)
        assert h["degraded"] is True
        assert h["iv_dark_count"] == 5
        assert "DATA-DEGRADED RUN" in h["summary"]
        assert "5/10" in h["summary"]

    def test_closed_market_wording_blames_data_not_market(self):
        rows = [_row(vega=None) for _ in range(10)]
        h = screen_data_health(rows, market_open=False)
        assert "DATA issues to fix" in h["summary"]

    def test_missing_premiums_degrade(self):
        rows = [_row(premium=None) for _ in range(4)] + [_row() for _ in range(6)]
        h = screen_data_health(rows, market_open=True)
        assert h["degraded"] is True
        assert h["no_premium_count"] == 4

    def test_small_gaps_do_not_cry_wolf(self):
        # 1/10 dark is a per-symbol block, not a run-level alarm.
        rows = [_row(vega=None)] + [_row() for _ in range(9)]
        h = screen_data_health(rows, market_open=True)
        assert h["degraded"] is False

    def test_empty_rows_do_not_crash(self):
        h = screen_data_health([], market_open=False)
        assert h["degraded"] is False
