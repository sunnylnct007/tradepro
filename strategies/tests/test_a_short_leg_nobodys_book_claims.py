"""Every option leg the BROKER holds must be one our book claims.

Every other check on this desk reads OUR records. An unrecorded short option is
the one position that cannot be found that way — it is absent from the book by
definition. So this check reads the BROKER and asks whether our records explain
what it holds. Broker is the golden source.

The shape it exists for: on 4 Sep 2026 placements 404'd for days and left rows
reading placed=None beside positions that were real. A leg opened that way is
in no P&L, in no risk total, and nothing will ever close it.
"""
import datetime as dt

import pytest

from tradepro_strategies.cli import desk_check as D


TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _leg(market="XSP", strike=758, right="P", qty=-1.0, pnl=-44.22):
    occ = f"{market:<6} OCT2026 {strike} {right} [{market:<5} 261016{right}{int(strike*1000):08d} 100]"
    return {"isOption": True, "quantity": qty, "unrealisedAbs": pnl,
            "instrumentName": occ, "ticker": occ}


def _row(market="XSP", put=758, call=780, placed=True, closed=None, date=TODAY):
    return {"market": market, "expiry_kind": "monthly", "placed": placed,
            "put_strike": put, "call_strike": call,
            "close_trigger": closed, "exchange_date": date}


@pytest.fixture
def desk(monkeypatch):
    def _install(positions, decisions):
        def _fake(base, token, path, timeout=30):
            if "positions" in path:
                return {"positions": positions}
            return {"rows": decisions}
        monkeypatch.setattr(D, "_get", _fake)
    return _install


def test_a_leg_no_row_claims_is_BROKEN(desk):
    desk([_leg()], [])
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert c.status == D.BROKEN
    assert "NO placement record claims this leg" in c.detail
    assert "SHORT" in c.detail, "the direction must be stated — a short is the risk"


def test_the_money_is_stated_not_just_the_fact(desk):
    # A warning must carry the number.
    desk([_leg(pnl=-44.22)], [])
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert "-44.22" in c.detail, c.detail


def test_a_leg_the_book_calls_CLOSED_is_BROKEN(desk):
    # A close was recorded that did not happen at the broker — the 1 Sep shape,
    # where the close ran, logged, and left four legs on overnight.
    desk([_leg()], [_row(closed="end_of_day")])
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert c.status == D.BROKEN
    assert "the book says CLOSED" in c.detail


def test_todays_open_leg_is_fine(desk):
    # Between the 14:12Z entry and the 19:45Z time exit, an open leg is the
    # strategy working. Flagging it would cry wolf every session.
    desk([_leg(), _leg(strike=780, right="C", pnl=0.79)], [_row()])
    checks = D.check_option_legs_vs_book("http://x", None)
    assert all(c.status == D.OK for c in checks), [c.detail for c in checks]


def test_a_leg_carried_OVERNIGHT_warns(desk):
    # Same-day close is the whole design; a leg still open from an earlier
    # session is the exposure the time exit exists to prevent.
    desk([_leg()], [_row(date=YESTERDAY)])
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert c.status == D.WARN
    assert YESTERDAY in c.detail


def test_a_SPY_row_does_not_vouch_for_an_XSP_leg(desk):
    # SPY and XSP are both about a tenth of the S&P and quote nearly identical
    # strikes. Matching on the number alone would let one market's row confirm
    # another's orphan — the check would bless exactly what it exists to catch.
    desk([_leg(market="XSP")], [_row(market="SPY")])
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert c.status == D.BROKEN, "a SPY row must not claim an XSP leg"


def test_no_legs_at_all_is_OK(desk):
    desk([], [])
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert c.status == D.OK


def test_an_unreadable_broker_is_UNKNOWN_never_OK(desk, monkeypatch):
    # Absence of evidence is not evidence of absence: if we cannot see the
    # broker we must not report that nothing is open.
    def _boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(D, "_get", _boom)
    c = D.check_option_legs_vs_book("http://x", None)[0]
    assert c.status == D.UNKNOWN
