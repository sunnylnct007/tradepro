"""An order born into a closed market is a dead order.

Measured 19 Sep 2026 across this strategy's entire OMS history:

    raised inside 13:30-20:00 UTC : BUY 12/34 filled
    raised outside                : BUY 0/10, SELL 0/36  — nothing, ever

It is not a buy/sell asymmetry, it is the placement window. An order raised at
04:00 is swept `stale_pending_auto_clean` or `superseded by newer order` long
before the bell, and the daemon re-runs every 15 minutes and raises another to
be swept in turn. ARWR's and SNOW's exits churned 36 orders overnight while the
decision behind them was correct the whole time.

The DECISION still happens on the settled close, as the backtest does. Only
the order waits for the session.
"""
from datetime import UTC, datetime

import pytest

from tradepro_strategies.paper.market_hours import is_open
from tradepro_strategies.paper.strategies import mean_reversion_swing as m


class _Bare(m.MeanReversionSwingStrategy):
    def __init__(self):
        self.strategy_id = "mean_reversion_swing_ibkr"
        self.decisions = []
        # the live daemon sets this; replay and unit tests leave it off
        self.enforce_placement_window = True

    def log_decision(self, **kw):
        self.decisions.append(kw)


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 9, 18, 14, 0, tzinfo=UTC), True),    # Friday, mid-session
    (datetime(2026, 9, 18, 13, 31, tzinfo=UTC), True),   # just after the bell
    (datetime(2026, 9, 18, 4, 0, tzinfo=UTC), False),    # overnight — the ARWR case
    (datetime(2026, 9, 18, 21, 0, tzinfo=UTC), False),   # after the close
    (datetime(2026, 9, 19, 14, 0, tzinfo=UTC), False),   # Saturday
    (datetime(2026, 9, 20, 14, 0, tzinfo=UTC), False),   # Sunday
])
def test_orders_are_only_placeable_inside_the_session(when, expected):
    s = _Bare()
    assert s.placeable_now("ARWR", when, now=when) is expected


def _fixed(when):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    return _DT


def test_a_deferred_order_is_recorded_not_silently_dropped():
    """The decision must remain visible — deferring is not discarding.

    Both refusal branches are checked. The original picked 19 Sep 04:00, which
    is a SATURDAY — so since the 20 Sep split it takes the calendar branch, not
    the session one. That is the correct answer for that timestamp and the
    labels must differ: "the exchange is closed today" and "it is outside the
    regular session but trading" are different facts, and a reader who cannot
    tell them apart cannot tell why an exit went through and an entry did not.
    """
    # WEEKEND — nothing fills, entry or exit.
    when = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)      # Saturday
    s = _Bare()
    assert s.placeable_now("ARWR", when, now=when) is False
    assert len(s.decisions) == 1
    d = s.decisions[0]
    assert d["action"] == "defer-exchange-closed"
    assert "weekend or holiday" in d["reason"]
    assert d["symbol"] == "ARWR"

    # TRADING DAY, OUTSIDE THE SESSION — an entry still waits, and the reason
    # keeps the number that justified the rule.
    weekday_pre = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)   # Friday, 00:00 ET
    s2 = _Bare()
    assert s2.placeable_now("ARWR", weekday_pre, now=weekday_pre) is False
    d2 = s2.decisions[0]
    assert d2["action"] == "defer-market-shut"
    assert "0 of 46" in d2["reason"]         # states the number


def test_nothing_is_logged_when_the_market_is_open():
    when = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
    s = _Bare()
    assert s.placeable_now("ARWR", when, now=when) is True
    assert s.decisions == []


def test_the_guard_uses_the_shared_helper_not_its_own_clock_arithmetic():
    """One rule, one object — market hours are defined in market_hours.py."""
    import inspect
    from tradepro_strategies.paper.strategy import Strategy
    src = inspect.getsource(Strategy.placeable_now)
    assert "is_open" in src
    assert "13" not in src.split('"""')[0].replace("13:30", "")  # no hand-rolled bounds


def test_replay_is_never_gated_by_the_wall_clock():
    """A backtest must not consult the clock — the flag is off by default."""
    s = _Bare()
    s.enforce_placement_window = False
    when = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
    assert s.placeable_now("ARWR", when, now=when) is True
    assert s.decisions == []


def test_the_live_daemon_turns_the_guard_on():
    """The flag is useless unless something sets it. paper_session must."""
    import pathlib
    src = pathlib.Path("tradepro_strategies/cli/paper_session.py").read_text()
    assert src.count("enforce_placement_window = True") >= 2, \
        "both live daemons (swing and ichimoku) must switch the guard on"


def test_the_helper_itself_still_agrees_about_a_known_session():
    assert is_open("us_equity", datetime(2026, 9, 18, 15, 0, tzinfo=UTC)) is True
    assert is_open("us_equity", datetime(2026, 9, 19, 15, 0, tzinfo=UTC)) is False
