"""Max loss and max gain, before the trade is placed.

Owner, 20 Oct 2026: *"options we should be able to determine as well on spread
the max loss and gain"*.

THE TRADE THIS EXISTS FOR. SKHY, 28 Sep: sold the 165 put for 5.10 — a credit
of $510. The row showed a credit and a delta. It did not show that the loss was
unbounded, and the position is now past -$1,998 and still open. As a 165/155
spread the worst case would have been fixed at roughly -$490 on the day it was
opened, incapable of growing.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.quant_engine.options.economics import (
    MULTIPLIER, bull_put_spread, cash_secured_put, compare_naked_vs_spread,
)


def test_cash_secured_put_max_gain_is_the_credit():
    e = cash_secured_put(strike=165.0, credit_per_share=5.10)
    assert e.max_gain == pytest.approx(510.0)


def test_cash_secured_put_says_its_loss_is_UNBOUNDED():
    """A CSP is often called 'defined risk because you'd own it anyway'. The
    row must still say the loss runs to zero, or it is the SKHY row again."""
    e = cash_secured_put(strike=165.0, credit_per_share=5.10)
    assert e.unbounded is True
    assert e.max_loss == pytest.approx(165.0 * MULTIPLIER - 510.0)


def test_cash_secured_put_breakeven_is_strike_less_credit():
    e = cash_secured_put(strike=300.0, credit_per_share=12.17)
    assert e.breakeven == pytest.approx(287.83)


def test_the_SKHY_case_a_spread_would_have_capped_it():
    """165/155, the trade that should have been placed."""
    e = bull_put_spread(short_strike=165.0, long_strike=155.0,
                        short_credit_per_share=5.10, long_debit_per_share=0.20)
    assert e.unbounded is False
    assert e.max_gain == pytest.approx(490.0)
    assert e.max_loss == pytest.approx(510.0)      # 1000 width - 490 credit
    assert e.max_loss < 1998, "the actual naked loss, which a spread cannot reach"


def test_spread_capital_is_the_width_not_the_strike():
    """The other half of the argument: a spread frees capital. 165 naked ties up
    $16,500; the 10-wide spread ties up $1,000."""
    naked = cash_secured_put(strike=165.0, credit_per_share=5.10)
    spread = bull_put_spread(short_strike=165.0, long_strike=155.0,
                             short_credit_per_share=5.10, long_debit_per_share=0.20)
    assert naked.capital == pytest.approx(16_500.0)
    assert spread.capital == pytest.approx(1_000.0)


def test_return_on_risk_is_gain_over_loss_not_over_capital():
    e = bull_put_spread(short_strike=100.0, long_strike=90.0,
                        short_credit_per_share=3.0, long_debit_per_share=1.0)
    # net 200, width 1000, loss 800
    assert e.max_gain == pytest.approx(200.0)
    assert e.max_loss == pytest.approx(800.0)
    assert e.return_on_risk == pytest.approx(0.25)


def test_a_spread_cannot_be_built_upside_down():
    """Selling the LOWER strike is a different trade with a different risk."""
    with pytest.raises(ValueError, match="HIGHER"):
        bull_put_spread(short_strike=90.0, long_strike=100.0,
                        short_credit_per_share=3.0, long_debit_per_share=1.0)


def test_comparison_states_what_the_floor_COSTS():
    """The spread always earns less. Showing both is how it gets decided
    rather than argued."""
    c = compare_naked_vs_spread(strike=165.0, credit_per_share=5.10,
                                width=10.0, long_debit_per_share=0.20)
    assert c["giveUp"] == pytest.approx(20.0)          # 510 -> 490
    assert c["lossAvoided"] > 15_000                   # 16,500-ish -> 510
    assert c["capitalFreed"] == pytest.approx(15_500.0)


def test_a_credit_wider_than_the_width_cannot_report_negative_loss():
    """Arbitrage-shaped input must not produce a nonsense row."""
    e = bull_put_spread(short_strike=100.0, long_strike=95.0,
                        short_credit_per_share=9.0, long_debit_per_share=0.0)
    assert e.max_loss == 0.0


def test_every_number_is_per_contract_in_dollars():
    """The owner's book is 1-lot. Percentages hide the thing that matters."""
    e = cash_secured_put(strike=10.0, credit_per_share=1.0)
    assert e.max_gain == 100.0 and e.capital == 1_000.0


# ── the screen checks its own arithmetic (7 Nov 2026) ─────────────────────
#
# These bounds are not opinions about value — they are what the contract
# cannot do. A vertical credit larger than its width would be money for
# nothing, which the market does not leave lying about: it means OUR number
# is wrong, not that we have found an edge.
#
# A bug detector wearing an arbitrage hat. The economics feed position sizing
# and a CLOSE verdict on a live account, and an IMPOSSIBLE figure is far more
# dangerous than an unattractive one, because impossible numbers look
# authoritative.

def test_a_sound_spread_reports_no_violation():
    from tradepro_strategies.quant_engine.options.economics import (
        bounds_violation, bull_put_spread)
    e = bull_put_spread(short_strike=100, long_strike=95,
                        short_credit_per_share=3.0, long_debit_per_share=1.0)
    assert bounds_violation(e, short_strike=100, long_strike=95) is None


def test_a_credit_bigger_than_the_width_is_caught():
    from tradepro_strategies.quant_engine.options.economics import (
        bounds_violation, bull_put_spread)
    e = bull_put_spread(short_strike=100, long_strike=95,
                        short_credit_per_share=9.0, long_debit_per_share=1.0)
    v = bounds_violation(e, short_strike=100, long_strike=95)
    assert v and "riskless profit" in v


def test_a_breakeven_above_the_short_strike_is_caught():
    from tradepro_strategies.quant_engine.options.economics import (
        Economics, bounds_violation)
    e = Economics(max_gain=100, max_loss=400, breakeven=105.0, capital=500,
                  return_on_risk=0.25, unbounded=False)
    v = bounds_violation(e, short_strike=100, long_strike=95)
    assert v and "break even above" in v


def test_a_naked_put_is_checked_too():
    from tradepro_strategies.quant_engine.options.economics import (
        bounds_violation, cash_secured_put)
    e = cash_secured_put(strike=100, credit_per_share=4.0)
    assert bounds_violation(e, short_strike=100, long_strike=None) is None


def test_an_inverted_spread_is_not_a_bull_put_spread():
    from tradepro_strategies.quant_engine.options.economics import (
        Economics, bounds_violation)
    e = Economics(max_gain=100, max_loss=100, breakeven=99.0, capital=200,
                  return_on_risk=1.0, unbounded=False)
    v = bounds_violation(e, short_strike=95, long_strike=100)
    assert v and "not a bull put spread" in v
