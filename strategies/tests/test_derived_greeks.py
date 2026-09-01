"""Theta, vega and gamma for the selected leg — derived, labelled, never zero-filled.

Owner, 1 Sep 2026: "we need more analysis paraneters on analysis screen. it
doenst display as pro".

"Analyze" opened a payoff chart seeded with SEVEN fields and discarded the rest
of the row. Worse, the row itself only ever carried DELTA — so the screen could
not have shown theta even if the modal wanted it.

For a premium SELLER theta is the headline number: it is the daily income rate,
the entire reason the position exists. "A 0.27-delta put paying $2.37" says
nothing about whether you are paid enough per day to carry the assignment risk.

`OptionQuote` carries a broker delta when IBKR serves tick greeks, but has no
field for theta, gamma or vega. These are DERIVED from the same Black-Scholes
pricer, at the same IV the row already displays, on the same strike/spot/DTE —
so every number is reproducible by hand from the row, and the UI labels them
"model" rather than passing a computed theta off as a broker quote.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli.options_screen import _derived_greeks
from tradepro_strategies.quant_engine.options.black_scholes import BlackScholesPricer

P = BlackScholesPricer()


def test_it_returns_the_greeks_a_seller_decides_on():
    g = _derived_greeks(P, spot=91.0, strike=91.0, dte=32, iv=0.40)
    assert set(g) == {"theta_per_day", "gamma", "vega_per_1pct",
                      "model_delta", "greeks_basis"}
    assert g["greeks_basis"] == "black_scholes_from_row_iv"


def test_theta_is_negative_per_day_and_material():
    """Negative: a long option decays. The SELLER earns it, and the UI shows the
    earning. Roughly -$6/contract/day on an ATM 32-day at 40% vol."""
    g = _derived_greeks(P, spot=91.0, strike=91.0, dte=32, iv=0.40)
    assert g["theta_per_day"] < 0
    per_contract = abs(g["theta_per_day"]) * 100
    assert 3.0 < per_contract < 12.0, per_contract


def test_decay_accelerates_toward_expiry():
    """The property that makes short-dated premium attractive and dangerous."""
    far = abs(_derived_greeks(P, 91.0, 91.0, 60, 0.40)["theta_per_day"])
    near = abs(_derived_greeks(P, 91.0, 91.0, 7, 0.40)["theta_per_day"])
    assert near > far, (near, far)


def test_a_put_delta_is_negative_and_atm_is_about_half():
    g = _derived_greeks(P, spot=91.0, strike=91.0, dte=32, iv=0.40)
    assert -0.60 < g["model_delta"] < -0.35, g["model_delta"]


def test_further_otm_has_a_smaller_delta():
    atm = _derived_greeks(P, 100.0, 100.0, 30, 0.35)["model_delta"]
    otm = _derived_greeks(P, 100.0, 85.0, 30, 0.35)["model_delta"]
    assert abs(otm) < abs(atm), (otm, atm)


def test_vega_is_positive_and_falls_as_expiry_nears():
    far = _derived_greeks(P, 100.0, 100.0, 90, 0.35)["vega_per_1pct"]
    near = _derived_greeks(P, 100.0, 100.0, 5, 0.35)["vega_per_1pct"]
    assert far > near > 0, (far, near)


@pytest.mark.parametrize("kw", [
    {"spot": None, "strike": 91.0, "dte": 32, "iv": 0.40},
    {"spot": 91.0, "strike": None, "dte": 32, "iv": 0.40},
    {"spot": 91.0, "strike": 91.0, "dte": None, "iv": 0.40},
    {"spot": 91.0, "strike": 91.0, "dte": 32, "iv": None},
    {"spot": 91.0, "strike": 91.0, "dte": 32, "iv": 0.0},
    {"spot": 91.0, "strike": 91.0, "dte": 0, "iv": 0.40},
])
def test_missing_inputs_yield_NOTHING_not_zeros(kw):
    """A zero theta reads as 'no decay', which is a claim ABOUT THE TRADE.
    Absent means 'we could not compute it'. The row must not conflate them."""
    assert _derived_greeks(P, **kw) == {}


def test_it_never_raises_into_the_screen():
    """An analysis extra may not break a screen that is otherwise fine."""
    assert _derived_greeks(P, "not-a-number", 91.0, 32, 0.40) == {}
