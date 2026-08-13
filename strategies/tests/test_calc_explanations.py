"""Every figure on a wheel row must be reproducible by hand (owner, 13 Aug
2026: "options is all about parameters and calculations ... all figures
explained and backed by calculations")."""
from __future__ import annotations

import math

from tradepro_strategies.cli.options_screen import explain_calcs


BASE = dict(symbol="SLV", spot=57.50, strike=54.0, premium=1.32, dte=39,
            bid=1.30, ask=1.34, spread=0.04, iv=0.459, hv30=0.425,
            delta=0.263, nav_gbp=118000.0, div_yield=None)


def test_every_value_matches_its_own_formula_arithmetic():
    c = explain_calcs(**BASE)
    # annualised yield: 1.32/54 * 365/39 * 100
    assert c["annualised_yield_pct"]["value"] == round((1.32 / 54) * (365 / 39) * 100, 1)
    assert c["max_profit_usd"]["value"] == 132.0
    assert c["breakeven_and_basis"]["value"] == 52.68        # 54 - 1.32
    assert c["max_loss_usd"]["value"] == 5268.0              # 52.68 * 100
    assert c["otm_distance_pct"]["value"] == round((57.5 - 54) / 57.5 * 100, 1)
    assert c["iv_hv_ratio"]["value"] == round(0.459 / 0.425, 3)
    fwd = 57.5 * math.exp(0.04 * 39 / 365)
    assert abs(c["forward_price"]["value"] - round(fwd, 2)) < 0.02


def test_formula_strings_carry_the_actual_inputs():
    c = explain_calcs(**BASE)
    assert "1.32 ÷ 54 × 365 ÷ 39" in c["annualised_yield_pct"]["formula"]
    assert "54 − 1.32" in c["breakeven_and_basis"]["formula"]
    assert "(1.34 − 1.30)" in c["spread_pct_of_premium"]["formula"]
    for entry in c.values():
        assert entry["why"], "every figure needs a plain-language why"


def test_missing_inputs_omit_the_figure_never_guess():
    c = explain_calcs(**{**BASE, "premium": None})
    assert "annualised_yield_pct" not in c
    assert "max_profit_usd" not in c
    assert "otm_distance_pct" in c            # still computable from spot+strike
    c2 = explain_calcs(**{**BASE, "iv": None, "hv30": None})
    assert "iv_hv_ratio" not in c2
    c3 = explain_calcs(**{**BASE, "nav_gbp": None})
    assert "size_vs_nav_pct" not in c3


def test_dividend_caveat_is_stated_when_yield_unavailable():
    c = explain_calcs(**BASE)
    assert "q=0" in c["forward_price"]["why"]
    c2 = explain_calcs(**{**BASE, "div_yield": 0.012})
    assert "q=0" not in c2["forward_price"]["why"]
