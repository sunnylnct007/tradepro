"""The advertised %/yr assumes a mid fill. The row must say so.

Measured 16 Sep 2026 on 7,809 of our own captured contract-pairs
(THETA_EARLY_CLOSE_GATES_V1.md): the median put bid-ask is 8.9% of mid, the
mean 28.2%, and crossing it is the largest single cost in the trade — bigger
than any parameter on the lane. The screen computed its headline yield from
q.mid and never mentioned that a seller is filled at the bid, so MO advertised
12.5%/yr on a 12.9% spread.

This is the owner's standing rule that a warning must state the number, applied
to the number itself.
"""
from tradepro_strategies.cli.options_screen import _fill_caveat


def test_it_names_both_yields_and_the_order_type():
    txt = _fill_caveat({"annualized_yield_pct": 12.5,
                        "annualized_yield_at_bid_pct": 11.7})
    assert "12.5" in txt and "11.7" in txt
    assert "MID" in txt
    assert "limit order" in txt and "never a market order" in txt


def test_it_stays_quiet_when_the_two_agree():
    """A caveat on every row is a caveat nobody reads."""
    assert _fill_caveat({"annualized_yield_pct": 12.5,
                         "annualized_yield_at_bid_pct": 12.4}) == ""


def test_it_stays_quiet_rather_than_guessing_when_the_bid_is_unknown():
    assert _fill_caveat({"annualized_yield_pct": 12.5,
                         "annualized_yield_at_bid_pct": None}) == ""
    assert _fill_caveat({"annualized_yield_pct": None,
                         "annualized_yield_at_bid_pct": 11.7}) == ""
    assert _fill_caveat({}) == ""


def test_a_wide_spread_produces_a_visibly_worse_number():
    """BMY on 16 Sep: 14.2% spread. The gap must be plain, not rounded away."""
    txt = _fill_caveat({"annualized_yield_pct": 20.0,
                        "annualized_yield_at_bid_pct": 17.2})
    assert "20.0" in txt and "17.2" in txt
