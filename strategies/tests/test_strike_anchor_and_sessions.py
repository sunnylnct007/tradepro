"""Gate on the settled close; anchor strikes to the session open.

THE BUG (31 Aug 2026). The backtest centres strikes on the DAY'S OPEN while
gating on the PREVIOUS close — no lookahead, current strikes. The live strangle
email used the previous close for BOTH. So the 04:00 job published NIFTY
23,950 / 24,450 off Friday's 24,175 close; by the open the index was 24,065,
leaving the put 116 points away and the call 384. Not a balanced strangle, and
not the trade any published figure describes.

Owner: "we need to wait for market open and then decide", "we need to ensure we
deal with diff exchange timings", and on the character of the thing — "this
option is supposed to be a slow boring and safe strategy". Hence: no intraday
polling, no chasing the open; before the open the row says PROVISIONAL.
"""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.cli import index_strangle_paper as P


def _at(tz_hour_local: str, cfg: dict, day: str = "2026-09-01"):
    """Build a UTC instant from an exchange-local wall clock."""
    from zoneinfo import ZoneInfo
    h, m = (int(x) for x in tz_hour_local.split(":"))
    local = dt.datetime.fromisoformat(day).replace(
        hour=h, minute=m, tzinfo=ZoneInfo(cfg["tz"]))
    return local.astimezone(dt.UTC)


def test_every_market_declares_its_exchange_hours():
    """Without these the screen cannot know whether a bar is settled."""
    for m, cfg in P.MARKETS.items():
        for k in ("tz", "open_local", "close_local"):
            assert k in cfg, f"{m} missing {k}"


def test_india_and_us_sessions_are_judged_in_their_own_timezone():
    """The whole point of "deal with diff exchange timings": 13:00 UTC is
    pre-open in New York and mid-session in Mumbai."""
    ind, us = P.MARKETS["NIFTY"], P.MARKETS["SPY"]
    # 09:00 local, before either open
    assert P._session_state(ind, _at("09:00", ind))[0] == "pre_open"
    assert P._session_state(us, _at("09:00", us))[0] == "pre_open"
    # 11:00 local, both trading
    assert P._session_state(ind, _at("11:00", ind))[0] == "open"
    assert P._session_state(us, _at("11:00", us))[0] == "open"
    # after each close
    assert P._session_state(ind, _at("16:00", ind))[0] == "closed"
    assert P._session_state(us, _at("17:00", us))[0] == "closed"


def test_a_weekend_is_never_in_flight():
    """Saturday must not be treated as a live session — otherwise the gate
    would discard the last settled close for a bar that cannot exist."""
    cfg = P.MARKETS["SPY"]
    assert P._session_state(cfg, _at("11:00", cfg, day="2026-09-05"))[0] == "closed"


def test_indias_open_is_earlier_than_the_us_open_in_utc():
    """A sanity check on the timings themselves — if these were swapped the
    jobs would fire on the wrong side of each open and never say so."""
    ind, us = P.MARKETS["NIFTY"], P.MARKETS["SPY"]
    assert _at("09:15", ind, "2026-09-01") < _at("09:30", us, "2026-09-01")


def test_provisional_is_stated_not_implied():
    """Before the open there is no opening price. The row must SAY the strikes
    are provisional rather than dress a stale close as tradeable — that is what
    produced the lopsided 31 Aug strangle."""
    src = open(P.__file__).read()
    assert 'spot_basis, provisional = "prior_close", True' in src
    assert 'spot_basis, provisional = "session_open", False' in src
    assert "PROVISIONAL" in src


def test_the_gate_never_reads_an_in_flight_bar():
    """Measured 31 Aug: mid-session, Yahoo already served an India VIX row
    stamped that day. Gating on it reintroduces the lookahead the backtest was
    corrected for."""
    src = open(P.__file__).read()
    assert "settled = [d for d in common if not (d == local_today and state != \"closed\")]" in src
