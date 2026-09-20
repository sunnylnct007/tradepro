"""An exit may work the extended session. Nothing works on a Saturday.

Two failures, found together on 20 Sep 2026.

THE BUG. The swing sleeve raised 34 SELL exit orders on Saturday 19 Sep between
00:05Z and 12:17Z — 28 cancelled "superseded by newer order" as the */15 daemon
replaced its own, 6 swept "stale_pending_auto_clean". The exchange was shut all
day. Not one could ever have filled.

THE THING THAT MAKES THE OBVIOUS FIX WRONG. Owner, 20 Sep: "som of the orders
execute outsode us hours as well so tey ca close." A blanket 13:30-20:00 UTC
rule would block legitimate pre/post-session exits. Blocking an exit is not the
safe direction — an entry deferred is a missed opportunity; an exit deferred is
an open risk nobody chose to keep.

So the gate asks two different questions:

    ENTRY  needs the REGULAR SESSION   (0 of 46 outside it ever filled)
    EXIT   needs only a TRADING DAY    (extended session is real; Saturday is not)
"""
import datetime as dt

import pytest

from tradepro_strategies.paper.market_hours import (
    is_open, is_trading_day, is_us_equity_trading_day,
)

SAT = dt.datetime(2026, 9, 19, 12, 17, tzinfo=dt.UTC)   # the real 34-order window
SUN = dt.datetime(2026, 9, 20, 15, 0, tzinfo=dt.UTC)
FRI_RTH = dt.datetime(2026, 9, 18, 15, 0, tzinfo=dt.UTC)
FRI_PRE = dt.datetime(2026, 9, 18, 11, 0, tzinfo=dt.UTC)   # 07:00 ET, pre-market
FRI_POST = dt.datetime(2026, 9, 18, 21, 30, tzinfo=dt.UTC)  # 17:30 ET, post
XMAS = dt.datetime(2026, 12, 25, 15, 0, tzinfo=dt.UTC)      # NYSE holiday, a Friday


def test_a_weekend_is_not_a_trading_day():
    assert is_us_equity_trading_day(SAT) is False
    assert is_us_equity_trading_day(SUN) is False


def test_a_holiday_is_not_a_trading_day_even_on_a_weekday():
    assert XMAS.weekday() < 5          # it IS a weekday
    assert is_us_equity_trading_day(XMAS) is False


def test_out_of_hours_on_a_weekday_IS_still_a_trading_day():
    """The whole point. Pre/post is closed for ENTRIES, open for EXITS."""
    for when in (FRI_PRE, FRI_POST):
        assert is_open("us_equity", when) is False       # not the regular session
        assert is_trading_day("us_equity", when) is True  # but the exchange trades


def test_the_regular_session_is_both()  :
    assert is_open("us_equity", FRI_RTH) is True
    assert is_trading_day("us_equity", FRI_RTH) is True


def test_an_unmodelled_class_fails_OPEN():
    # Same rule is_open uses. A gate that blocks a class it does not understand
    # silently stops a strategy nobody knew was gated.
    assert is_trading_day("something_new", SAT) is True


# ── the guard itself ────────────────────────────────────────────────

class _Strat:
    """Minimal stand-in exposing what placeable_now touches."""
    enforce_placement_window = True
    placement_asset_class = "us_equity"
    def __init__(self): self.decisions = []
    def log_decision(self, **kw): self.decisions.append(kw)

    # borrow the real implementation
    from tradepro_strategies.paper.strategy import Strategy
    placeable_now = Strategy.placeable_now


@pytest.mark.parametrize("when,is_exit,expected,label", [
    (SAT,      True,  False, "exit on SATURDAY — the 34-order bug"),
    (SAT,      False, False, "entry on Saturday"),
    (SUN,      True,  False, "exit on Sunday"),
    (XMAS,     True,  False, "exit on a holiday"),
    (FRI_PRE,  True,  True,  "EXIT pre-market on a trading day — must pass"),
    (FRI_POST, True,  True,  "EXIT post-market on a trading day — must pass"),
    (FRI_PRE,  False, False, "entry pre-market — waits for the open"),
    (FRI_POST, False, False, "entry post-market — waits for the open"),
    (FRI_RTH,  True,  True,  "exit in the regular session"),
    (FRI_RTH,  False, True,  "entry in the regular session"),
])
def test_the_gate(when, is_exit, expected, label):
    s = _Strat()
    assert s.placeable_now("ARWR", when, now=when, is_exit=is_exit) is expected, label


def test_a_blocked_weekend_order_says_the_exchange_is_CLOSED_not_merely_shut():
    """The two refusals are different facts and must read differently."""
    s = _Strat()
    s.placeable_now("ARWR", SAT, now=SAT, is_exit=True)
    assert s.decisions[-1]["action"] == "defer-exchange-closed"

    s2 = _Strat()
    s2.placeable_now("ARWR", FRI_PRE, now=FRI_PRE, is_exit=False)
    assert s2.decisions[-1]["action"] == "defer-market-shut"
    # ...and the entry refusal must say an exit WOULD have passed, or the
    # reader cannot tell why their close went through and their buy did not.
    assert "EXIT would pass" in s2.decisions[-1]["reason"]


def test_the_window_stays_off_by_default_for_replay_and_tests():
    class Off(_Strat):
        enforce_placement_window = False
    assert Off().placeable_now("ARWR", SAT, now=SAT, is_exit=False) is True
