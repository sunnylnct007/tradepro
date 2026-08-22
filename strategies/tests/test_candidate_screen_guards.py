"""Guards shared by the Swing and Momentum candidate screens.

Both screens put an ENTRY and a STOP in front of a human who may place that
order. So the guards that decide what is allowed to become a row are the part
worth pinning: a wrong row here is a wrong trade, not a wrong number.

Written after a momentum run surfaced HG=F (copper futures) and ^STOXX (an
index) as candidates, each with an entry price and an 8% stop, as if they were
shares you could buy. Nothing rejected them — the universe filter was
`"." not in symbol`, which only ever excluded foreign listings.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli import momentum_candidates as mom
from tradepro_strategies.cli import swing_candidates as swing


@pytest.mark.parametrize("module", [mom, swing], ids=["momentum", "swing"])
class TestTradeableUniverse:
    """Both screens must apply the SAME filter — they read the same cache dir."""

    @pytest.mark.parametrize("sym", ["AAPL", "MU", "PLTR", "SPY", "BRK-B"])
    def test_ordinary_us_listings_are_tradeable(self, module, sym):
        assert module._tradeable(sym) is True

    @pytest.mark.parametrize("sym", [
        "HG=F",       # copper futures — different contract mechanics entirely
        "CL=F",
        "^STOXX",     # an index; not directly tradeable at all
        "^GSPC",
        "0700.HK",    # foreign listing; no IBKR entitlement, unmapped ticker
        "AIR.PA",
        "BTC-USD",    # crypto pair
        "",
    ])
    def test_untradeable_instruments_are_excluded(self, module, sym):
        assert module._tradeable(sym) is False, (
            f"{sym!r} would be rendered as a placeable row with an entry and a stop"
        )


@pytest.mark.parametrize("module", [mom, swing], ids=["momentum", "swing"])
class TestPoisonCheck:
    """Wrong-venue series are internally consistent, so NaN and spike checks
    pass them. The only tell is that the historical range dwarfs the present
    one — a different instrument's prices carried under this symbol."""

    def test_clean_series_survives(self, module):
        ok, _ = module.poison_check([100.0 + i * 0.1 for i in range(300)])
        assert ok is True

    def test_wrong_venue_series_is_dropped(self, module):
        # 300 bars near 50, with an early history near 900 — the shape of a
        # local listing's prices stored under a US ADR's symbol.
        prices = [900.0] * 30 + [50.0] * 270
        ok, ratio = module.poison_check(prices)
        assert ok is False
        assert ratio >= 6

    def test_a_genuine_multiyear_rally_is_not_called_poison(self, module):
        # The check must not fire on a name that legitimately ran up: the
        # historical MAX being far above the recent median is normal for a
        # winner, so the test is max-vs-median, and a riser's max IS recent.
        prices = [10.0 + i * 0.5 for i in range(300)]   # 10 -> 159, all upward
        ok, _ = module.poison_check(prices)
        assert ok is True


@pytest.mark.parametrize("module", [mom, swing], ids=["momentum", "swing"])
class TestSignalBarSelection:
    """Which bar is "today" — the single most consequential line in either
    screen, because it decides the price the owner would place an order at.

    Regression: the comparison was ">=", which stepped back an extra session
    and published a stale close. PLTR went out with an entry of 173.96 when
    the settled 21 Aug close was 179.94 (3.4% wrong), and 12 of 13 rows were
    triggers that had already expired the previous session.
    """

    def test_a_bar_dated_the_last_settled_session_is_used(self, module):
        dates = ["2026-08-19", "2026-08-20", "2026-08-21"]
        assert module._pick_signal_index(dates, "2026-08-21") == 2

    def test_an_in_progress_session_is_stepped_over(self, module):
        # The harvest writes a partial row for today mid-session.
        dates = ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24"]
        assert module._pick_signal_index(dates, "2026-08-21") == 2

    def test_a_store_lagging_behind_uses_its_own_newest_bar(self, module):
        # Harvest failed; newest stored bar predates the last settled session.
        # Use it (and let the screen's signal_bar date show the staleness)
        # rather than stepping back to an even older one.
        dates = ["2026-08-18", "2026-08-19"]
        assert module._pick_signal_index(dates, "2026-08-21") == 1


def test_both_screens_agree_on_which_bar_is_today():
    """They read the same store and are read side by side; if they disagreed
    about the signal bar, two screens would quote different prices for the
    same session."""
    dates = ["2026-08-20", "2026-08-21"]
    assert mom._pick_signal_index(dates, "2026-08-21") == \
           swing._pick_signal_index(dates, "2026-08-21")
