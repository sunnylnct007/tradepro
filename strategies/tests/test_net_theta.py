"""Daily decay income, net of the leg you bought.

The row's `theta_per_day` is the SHORT leg alone. On the 21 Oct board 62 of 82
rows were spreads, and for every one of them that figure overstated the daily
income — the long put you bought decays too, and its decay works against you.
"""
import pytest

from tradepro_strategies.cli.options_screen import _net_theta_per_day
from tradepro_strategies.quant_engine.options.black_scholes import BlackScholesPricer

P = BlackScholesPricer()
SPOT, DTE, IV = 100.0, 30, 0.30


def test_a_naked_short_keeps_the_whole_decay():
    net = _net_theta_per_day(P, SPOT, DTE, short_strike=95.0, short_iv=IV)
    assert net is not None and net > 0, "a premium seller earns decay"


def test_a_spread_earns_LESS_than_the_short_leg_alone():
    """THE BUG. The long leg bleeds against you every day."""
    naked = _net_theta_per_day(P, SPOT, DTE, short_strike=95.0, short_iv=IV)
    spread = _net_theta_per_day(P, SPOT, DTE, short_strike=95.0, short_iv=IV,
                                long_strike=90.0, long_iv=IV)
    assert spread is not None and naked is not None
    assert 0 < spread < naked, (
        f"a spread cannot earn as much decay as the naked short it replaced "
        f"(spread {spread} vs naked {naked})")


def test_a_wider_spread_keeps_more_of_the_decay():
    # The further out the floor, the less it costs in theta — which is the
    # real trade-off behind choosing a width, and the reason this number has
    # to be visible per row rather than assumed.
    narrow = _net_theta_per_day(P, SPOT, DTE, short_strike=95.0, short_iv=IV,
                                long_strike=94.0, long_iv=IV)
    wide = _net_theta_per_day(P, SPOT, DTE, short_strike=95.0, short_iv=IV,
                              long_strike=80.0, long_iv=IV)
    assert narrow is not None and wide is not None
    assert wide > narrow


def test_missing_inputs_yield_None_not_zero():
    # A zero reads as "this trade earns nothing", which is a claim about the
    # trade. Absent means we could not compute it. The screen must not conflate
    # them — the same rule the rest of this file is built on.
    for kw in ({"short_strike": None, "short_iv": IV},
               {"short_strike": 95.0, "short_iv": None},
               {"short_strike": 95.0, "short_iv": 0.0}):
        assert _net_theta_per_day(P, SPOT, DTE, **kw) is None
    assert _net_theta_per_day(P, None, DTE, short_strike=95.0, short_iv=IV) is None
    assert _net_theta_per_day(P, SPOT, 0, short_strike=95.0, short_iv=IV) is None


def test_a_spread_whose_long_leg_has_no_iv_is_None_not_the_short_leg():
    # Falling back to the short leg's theta would silently reproduce exactly
    # the overstatement this function exists to remove.
    assert _net_theta_per_day(P, SPOT, DTE, short_strike=95.0, short_iv=IV,
                              long_strike=90.0, long_iv=None) is None
