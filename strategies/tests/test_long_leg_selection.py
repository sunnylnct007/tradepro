"""select_long_leg — where the floor of a bull put spread gets placed.

Chosen by DELTA, not a dollar width or a percentage, because the owner's rule
is "no hardcoded constant but variable per symbol" and delta is the only
distance measure that already carries the name's own volatility.

The refusal cases matter as much as the selection: a spread exists to have a
KNOWN max loss, so a long leg we cannot price must yield no number at all.
"""
import pytest

from tradepro_strategies.quant_engine.options.black_scholes import BlackScholesPricer
from tradepro_strategies.quant_engine.options.chains import (
    OptionQuote,
    delta_of,
    select_long_leg,
)
from tradepro_strategies.quant_engine.options.economics import bull_put_spread

PRICER = BlackScholesPricer()
SPOT = 100.0
T = 35 / 365.0


def _put(strike: float, *, iv: float = 0.30, bid: float = 1.0,
         ask: float = 1.1) -> OptionQuote:
    return OptionQuote(kind="put", strike=strike, bid=bid, ask=ask, iv=iv)


def _ladder(strikes, **kw) -> list[OptionQuote]:
    return [_put(k, **kw) for k in strikes]


def test_picks_a_strike_below_the_short_one():
    chain = _ladder([70, 75, 80, 85, 90, 95, 100])
    q = select_long_leg(chain, short_strike=90.0, spot=SPOT, t_years=T, pricer=PRICER)
    assert q is not None and q.strike < 90.0


def test_never_picks_at_or_above_the_short_strike():
    # A bull put spread sells the HIGHER strike. Picking at or above it would
    # build the opposite structure, and bull_put_spread() would raise.
    chain = _ladder([85, 90, 95, 100, 105])
    q = select_long_leg(chain, short_strike=90.0, spot=SPOT, t_years=T, pricer=PRICER)
    assert q is not None and q.strike < 90.0
    bull_put_spread(short_strike=90.0, long_strike=q.strike,
                    short_credit_per_share=2.0, long_debit_per_share=q.ask)


def test_the_floor_is_further_away_on_a_more_volatile_name():
    # THE WHOLE POINT, and the thing a fixed width cannot do. Same strikes,
    # same short leg, same expiry — only the name's volatility differs, and the
    # floor moves to match it.
    strikes = list(range(40, 101, 5))
    calm = select_long_leg(_ladder(strikes, iv=0.20), short_strike=90.0,
                           spot=SPOT, t_years=T, pricer=PRICER)
    wild = select_long_leg(_ladder(strikes, iv=0.90), short_strike=90.0,
                           spot=SPOT, t_years=T, pricer=PRICER)
    assert calm is not None and wild is not None
    assert wild.strike < calm.strike, (
        f"a 0.10-delta floor should sit FURTHER below on the volatile name, "
        f"got calm={calm.strike} wild={wild.strike}")


def test_lands_near_the_target_delta():
    chain = _ladder(list(range(40, 101, 1)))
    q = select_long_leg(chain, short_strike=95.0, spot=SPOT, t_years=T,
                        pricer=PRICER, target_abs_delta=0.10)
    assert q is not None
    assert abs(abs(delta_of(q, SPOT, T, PRICER)) - 0.10) < 0.04


def test_an_unpriceable_long_leg_yields_nothing():
    # We BUY this leg, so the debit is the ask. No ask means we cannot state
    # the max loss, and a spread whose max loss is guessed at defeats the
    # entire reason for buying the leg.
    chain = [_put(k, bid=0.0, ask=0.0) for k in (70, 75, 80, 85)]
    assert select_long_leg(chain, short_strike=90.0, spot=SPOT,
                           t_years=T, pricer=PRICER) is None


def test_no_strike_below_the_short_one_yields_nothing():
    chain = _ladder([90, 95, 100])
    assert select_long_leg(chain, short_strike=90.0, spot=SPOT,
                           t_years=T, pricer=PRICER) is None


def test_empty_chain_yields_nothing():
    assert select_long_leg([], short_strike=90.0, spot=SPOT,
                           t_years=T, pricer=PRICER) is None


def test_the_resulting_spread_has_a_finite_capped_loss():
    # The property the owner asked for: a deterministic loss, known on the day
    # the trade is opened.
    chain = _ladder(list(range(40, 101, 5)))
    q = select_long_leg(chain, short_strike=90.0, spot=SPOT, t_years=T, pricer=PRICER)
    e = bull_put_spread(short_strike=90.0, long_strike=q.strike,
                        short_credit_per_share=2.5, long_debit_per_share=q.ask)
    assert not e.unbounded
    assert 0 < e.max_loss < float("inf")
    assert e.max_loss <= (90.0 - q.strike) * 100
    assert e.capital == pytest.approx((90.0 - q.strike) * 100)
