"""Steps for horizons.feature — pin the spec §6.1 contract."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.horizons import classify_horizons


@given("VUKE.L horizon inputs from 8 May 2026")
def step_vuke_inputs(context):
    # `swing_score.total` set explicitly so the horizon test pins the
    # range-position modifier only (Bug #11: the swing horizon reads
    # the composite total from the row, then applies the range
    # modifier on top). VUKE.L sits at 72.6th pctile → -1 modifier;
    # base=3 → final 2 matches the spec-pinned target.
    context.row = {
        "market_state": {
            "rsi_14": 46, "pct_off_52w_high_pct": -5.2,
            "above_sma_200": True, "momentum_3m_pct": -0.5,
            "last_price": 44.74, "range_position_pct": 72.6,
        },
        "stats": {"sharpe": 0.92, "cagr_pct": 11.4},
        "swing_score": {"total": 3, "layers": {"event": 0}},
        "fundamentals": {
            "expense_ratio_pct": 0.09, "n_holdings": 100,
            "dividend_yield_pct": 3.08, "legal_type": "ETF",
        },
        "external_consensus": {"target_mean": 47.0},
        "valuation_flag": {"flag": "FAIR"},
    }


@given("GOOGL horizon inputs at 95th pctile of range")
def step_googl_inputs(context):
    context.row = {
        "market_state": {
            "rsi_14": 80, "pct_off_52w_high_pct": -0.6,
            "above_sma_200": True, "momentum_3m_pct": 4.0,
            "last_price": 200.0, "range_position_pct": 95.0,
        },
        "stats": {"sharpe": 0.85, "cagr_pct": 15.0},
        "swing_score": {"layers": {"event": 0}},
        "fundamentals": {"n_holdings": 1, "legal_type": "EQUITY"},
        "external_consensus": {"target_mean": 220.0},
        "valuation_flag": {"flag": "FAIR"},
    }


@given("NVDA horizon inputs as a single stock")
def step_nvda_inputs(context):
    context.row = {
        "market_state": {
            "rsi_14": 53, "pct_off_52w_high_pct": -8.0,
            "above_sma_200": True, "momentum_3m_pct": 5.0,
            "last_price": 198.0, "range_position_pct": 60.0,
        },
        "stats": {"sharpe": 1.06, "cagr_pct": 45.7},
        "swing_score": {"layers": {"event": 2}},
        "fundamentals": {
            "n_holdings": 1, "dividend_yield_pct": 0.02,
            "legal_type": "EQUITY",
        },
        "external_consensus": {"target_mean": 269.0},
        "valuation_flag": {"flag": "FAIR"},
    }


@given("a low-pctile symbol with RSI 35 and 12% off the high")
def step_low_pctile_symbol(context):
    # Same Bug #11 contract: pin the horizon's range modifier by
    # supplying composite total directly. 25th pctile → +1 modifier;
    # base=5 → final 6 satisfies "at least 6".
    context.row = {
        "market_state": {
            "rsi_14": 35, "pct_off_52w_high_pct": -12.0,
            "above_sma_200": True, "momentum_3m_pct": 2.0,
            "last_price": 100.0, "range_position_pct": 25.0,
        },
        "stats": {"sharpe": 0.75, "cagr_pct": 9.0},
        "swing_score": {"total": 5, "layers": {"event": 2}},  # catalyst present
        "fundamentals": {"n_holdings": 1, "legal_type": "EQUITY"},
        "external_consensus": {"target_mean": 120.0},
        "valuation_flag": {"flag": "CHEAP"},
    }


@given("a high-quality stock with Sharpe 1.0 and 30% analyst upside")
def step_quality_stock(context):
    context.row = {
        "market_state": {
            "rsi_14": 50, "pct_off_52w_high_pct": -10.0,
            "above_sma_200": True, "momentum_3m_pct": 4.0,
            "last_price": 100.0, "range_position_pct": 50.0,
        },
        "stats": {"sharpe": 1.0, "cagr_pct": 18.0},
        "swing_score": {"layers": {"event": 0}},
        "fundamentals": {"n_holdings": 1, "legal_type": "EQUITY"},
        "external_consensus": {"target_mean": 130.0},  # 30% upside
        "valuation_flag": {"flag": "CHEAP"},
    }


@when("I classify horizons")
def step_classify(context):
    context.horizons = classify_horizons(context.row)


@then('the swing signal is "{expected}"')
def step_swing_signal(context, expected: str):
    actual = context.horizons.swing.signal
    assert actual == expected, (
        f"swing: expected {expected!r}, got {actual!r} "
        f"(score={context.horizons.swing.score})"
    )


@then('the long_term signal is "{expected}"')
def step_long_term_signal(context, expected: str):
    actual = context.horizons.long_term.signal
    assert actual == expected, (
        f"long_term: expected {expected!r}, got {actual!r} "
        f"(score={context.horizons.long_term.score})"
    )


@then('the passive signal is "{expected}"')
def step_passive_signal(context, expected: str):
    actual = context.horizons.passive.signal
    assert actual == expected, (
        f"passive: expected {expected!r}, got {actual!r} "
        f"(score={context.horizons.passive.score})"
    )


@then('the swing score is {expected:d}')
def step_swing_score_eq(context, expected: int):
    actual = context.horizons.swing.raw_score
    assert actual == expected, f"swing score: expected {expected}, got {actual}"


@then('the swing score is at least {minimum:d}')
def step_swing_score_gte(context, minimum: int):
    actual = context.horizons.swing.raw_score or 0
    assert actual >= minimum, (
        f"swing score: expected ≥{minimum}, got {actual}"
    )


@then('the swing reasons mention "{snippet}"')
def step_swing_reasons_mention(context, snippet: str):
    reasons = context.horizons.swing.reasons
    found = any(snippet in r for r in reasons)
    assert found, f"swing reasons missing {snippet!r}: {reasons}"


@then('the passive reasons mention "{snippet}"')
def step_passive_reasons_mention(context, snippet: str):
    reasons = context.horizons.passive.reasons
    found = any(snippet in r for r in reasons)
    assert found, f"passive reasons missing {snippet!r}: {reasons}"


@then('the range_pct is {expected:f}')
def step_range_pct(context, expected: float):
    actual = context.horizons.range_pct
    assert actual is not None, "range_pct is None"
    assert abs(actual - expected) < 0.05, (
        f"range_pct: expected {expected}, got {actual}"
    )
