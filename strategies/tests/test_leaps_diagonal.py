"""LEAPS diagonal (poor-man's covered call) arithmetic.

The gate these tests exist for: **width must exceed the net debit.** If the
strikes are closer together than what you paid, the structure cannot make money
even when the trade goes exactly right — stock rises, short call assigned,
position closed at maximum value — and you lose on a CORRECT directional call.
It is invisible unless computed, which is the entire argument for this module.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.quant_engine.options.leaps_diagonal import (
    DiagonalLeg, evaluate_diagonal,
)


def _long(strike=100.0, price=35.0, dte=400, delta=0.85):
    return DiagonalLeg(strike=strike, price=price, dte=dte, delta=delta)


def _short(strike=140.0, price=3.0, dte=35, delta=0.25):
    return DiagonalLeg(strike=strike, price=price, dte=dte, delta=delta)


class TestTheStructuralGate:
    def test_width_below_debit_is_BLOCKED(self):
        """THE case. Long 100 at 35, short 130 at 3 → debit 32, width 30.
        Best case returns 30 against 32 paid: a guaranteed loss on a winning
        directional view."""
        e = evaluate_diagonal(spot=130.0,
                              long_leg=_long(strike=100.0, price=35.0),
                              short_leg=_short(strike=130.0, price=3.0))
        assert not e.ok
        assert any("STRUCTURALLY UNPROFITABLE" in b for b in e.blocks)
        assert e.max_profit_usd < 0

    def test_width_equal_to_debit_is_also_blocked(self):
        """Break-even at BEST case is not a trade."""
        e = evaluate_diagonal(spot=130.0,
                              long_leg=_long(strike=100.0, price=33.0),
                              short_leg=_short(strike=130.0, price=3.0))
        assert not e.ok
        assert e.max_profit_usd == pytest.approx(0.0, abs=1e-6)

    def test_healthy_structure_passes(self):
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        assert e.ok, e.blocks
        assert e.net_debit == pytest.approx(32.0)
        assert e.width == pytest.approx(40.0)
        assert e.max_profit_usd == pytest.approx(800.0)

    def test_negative_debit_is_refused_as_impossible(self):
        e = evaluate_diagonal(spot=120.0,
                              long_leg=_long(price=2.0),
                              short_leg=_short(price=5.0))
        assert not e.ok
        assert any("not positive" in b for b in e.blocks)


class TestArithmetic:
    def test_capital_at_risk_is_the_debit_not_the_strike(self):
        """The whole point versus a covered call: 100 shares of a $120 stock is
        $12,000; this is $3,200."""
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        assert e.capital_usd == pytest.approx(3200.0)
        assert e.max_loss_usd == pytest.approx(3200.0)

    def test_breakeven_is_long_strike_plus_debit(self):
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        assert e.breakeven == pytest.approx(132.0)

    def test_return_if_called(self):
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        assert e.return_if_called_pct == pytest.approx(25.0, abs=0.01)

    def test_static_return_and_annualisation(self):
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        # 3.00 premium on 32.00 debit = 9.375% per 35-day cycle
        assert e.static_return_pct == pytest.approx(9.375, abs=0.01)
        assert e.annualised_static_pct == pytest.approx(9.375 * 365 / 35, abs=0.5)

    def test_extrinsic_split(self):
        """Long 100 strike at 35 with spot 120 → intrinsic 20, extrinsic 15.
        Short 140 strike OTM → all 3.00 is extrinsic."""
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        assert e.long_extrinsic == pytest.approx(15.0)
        assert e.short_extrinsic == pytest.approx(3.0)

    def test_extrinsic_never_negative_on_a_sub_intrinsic_quote(self):
        e = evaluate_diagonal(spot=200.0,
                              long_leg=_long(strike=100.0, price=90.0),
                              short_leg=_short(strike=260.0, price=2.0))
        assert e.long_extrinsic == 0.0


class TestLegQuality:
    def test_shallow_leaps_delta_is_blocked(self):
        e = evaluate_diagonal(spot=120.0,
                              long_leg=_long(delta=0.55), short_leg=_short())
        assert not e.ok
        assert any("not deep enough ITM" in b for b in e.blocks)

    def test_short_dated_long_leg_is_not_a_leaps(self):
        e = evaluate_diagonal(spot=120.0,
                              long_leg=_long(dte=90), short_leg=_short())
        assert not e.ok
        assert any("not a LEAPS" in b for b in e.blocks)

    def test_slow_decaying_short_leg_warns(self):
        """If the short leg bleeds time value slower per day than the long one,
        theta is working against the structure."""
        e = evaluate_diagonal(spot=120.0,
                              long_leg=_long(price=35.0, dte=400),
                              short_leg=DiagonalLeg(strike=140.0, price=0.20,
                                                    dte=35, delta=0.10))
        assert any("decays SLOWER" in w for w in e.warnings)

    def test_itm_short_call_warns_about_early_assignment(self):
        e = evaluate_diagonal(spot=150.0,
                              long_leg=_long(price=55.0),
                              short_leg=_short(strike=140.0, price=12.0))
        assert any("early assignment" in w.lower() for w in e.warnings)

    def test_expensive_leaps_time_value_warns(self):
        e = evaluate_diagonal(spot=120.0,
                              long_leg=_long(strike=100.0, price=45.0),
                              short_leg=_short())
        assert any("time value" in w for w in e.warnings)

    def test_far_dated_short_leg_warns(self):
        e = evaluate_diagonal(spot=120.0, long_leg=_long(),
                              short_leg=_short(dte=120, price=8.0))
        assert any("theta curve" in w for w in e.warnings)


class TestCalcsAreShown:
    def test_every_headline_number_carries_its_arithmetic(self):
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        for k in ("net_debit", "width", "max_profit", "breakeven", "return_if_called"):
            assert k in e.calcs and e.calcs[k], f"{k} has no working shown"
        assert "35.00" in e.calcs["net_debit"] and "3.00" in e.calcs["net_debit"]

    def test_breakeven_note_declares_its_own_approximation(self):
        """The real breakeven is BETTER than stated (the LEAPS retains extrinsic
        at short expiry). Overstating the bar is the safe direction, but it must
        be said rather than left as a silent inaccuracy."""
        e = evaluate_diagonal(spot=120.0, long_leg=_long(), short_leg=_short())
        assert "approximate" in e.calcs["breakeven"]
