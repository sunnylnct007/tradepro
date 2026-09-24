"""A strangle's credit needs BOTH legs, or it is not the strangle's credit.

23 Sep 2026, XSP monthly. The broker reported an entry price for the call and
not the put, so `_credit_from_broker` summed what it had and returned it as the
pair's credit:

    credit_actual   284.78   the CALL alone (2.847797 x 100)
    exit_cost       981.19   both legs
    realised        -69.63   computed by the close from the true 911.56

A round trip that lost 69.63 read as -696.41 beside its own realised figure —
the two columns disagreeing by a factor of ten with nothing to say why. The
per-leg prices already followed the right rule (record the leg you have, leave
the other NULL, never imply a price nobody paid). The TOTAL did not.
"""
import pytest

from tradepro_strategies.cli import index_strangle_paper as P
from tradepro_strategies.cli import push_to_api


def _occ(right: str, strike: float) -> str:
    """An IBKR contract description _occ_strike can parse."""
    return f"XSP 261016{right}{int(round(strike * 1000)):08d}"


@pytest.fixture
def positions(monkeypatch):
    """Serve a fixed IBKR positions payload with no network."""
    def _install(*legs):
        payload = {"positions": [
            {"isOption": True, "quantity": -1, "multiplier": 100,
             "averagePricePaid": px, "instrumentName": _occ(right, k)}
            for right, k, px in legs]}

        class _R:
            @staticmethod
            def json():
                return payload

        monkeypatch.setattr(push_to_api, "load_credentials",
                            lambda *a, **k: ("http://x", "t"))
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    return _install


LEG = {"put_strike": 767.0, "call_strike": 789.0}


def test_both_legs_present_gives_the_pair_credit(positions):
    positions(("P", 767.0, 6.268), ("C", 789.0, 2.848))
    got = P._credit_from_broker({}, leg=LEG, expect_legs=2)
    assert got["credit"] == pytest.approx((6.268 + 2.848) * 100, abs=0.01)
    assert got["put_entry"] == pytest.approx(6.268)
    assert got["call_entry"] == pytest.approx(2.848)


def test_ONE_leg_records_NO_pair_credit(positions):
    # The 23 Sep shape: the call came back, the put did not.
    positions(("C", 789.0, 2.847797))
    got = P._credit_from_broker({}, leg=LEG, expect_legs=2)
    assert got["credit"] is None, \
        "a half pair must never be recorded as the pair's credit"
    # The leg we DO know is still kept — it grades the execution.
    assert got["call_entry"] == pytest.approx(2.847797)
    assert "put_entry" not in got


def test_the_understated_number_is_exactly_what_shipped_on_23_Sep(positions):
    # Pin the regression: without the guard this returns 284.78, which is what
    # the decision log recorded against an exit of 981.19.
    positions(("C", 789.0, 2.847797))
    unguarded = P._credit_from_broker({}, leg=LEG, expect_legs=1)
    assert unguarded["credit"] == pytest.approx(284.78, abs=0.01)
    guarded = P._credit_from_broker({}, leg=LEG, expect_legs=2)
    assert guarded["credit"] is None


def test_a_PARTIAL_placement_of_one_leg_DOES_record_its_credit(positions):
    # A partial fill is a one-legged position, so the single leg IS the whole
    # thing. Refusing a credit there would discard a real number.
    positions(("C", 789.0, 2.847797))
    got = P._credit_from_broker({}, leg=LEG, expect_legs=1)
    assert got["credit"] == pytest.approx(284.78, abs=0.01)


def test_no_legs_at_all_is_still_None(positions):
    positions()
    assert P._credit_from_broker({}, leg=LEG, expect_legs=2) is None
