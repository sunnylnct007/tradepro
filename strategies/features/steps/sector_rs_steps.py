"""Steps for sector_rs.feature.

Tests _rs_to_score (pure) and the curated SYMBOL_SECTOR_ETF map lookups
(pure dict, no network). compute_sector_rs is tested by patching
_price_return so no yfinance calls are made.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from behave import given, then, when

from tradepro_strategies.sector_rs import (
    _rs_to_score,
    get_sector_etf,
    compute_sector_rs,
    SYMBOL_SECTOR_ETF,
)


# ── _rs_to_score ───────────────────────────────────────────────────

@when("I call _rs_to_score with rs_pct={rs:f}")
def step_rs_to_score(context, rs):
    context._rs_score = _rs_to_score(rs)


@then("the score is {expected:d}")
def step_assert_score(context, expected):
    assert context._rs_score == expected, (
        f"expected score={expected}, got {context._rs_score}"
    )


# ── get_sector_etf ───────────────────────────────────────────────��

@when("I call get_sector_etf for \"{symbol}\"")
def step_get_sector_etf(context, symbol):
    context._etf, context._fallback = get_sector_etf(symbol)


@when("I call get_sector_etf for \"{symbol}\" via yfinance stub returning no sector")
def step_get_sector_etf_unknown(context, symbol):
    """Patch yfinance so Ticker.info returns an empty dict — no sector key."""
    mock_ticker = MagicMock()
    mock_ticker.info = {}
    with patch("tradepro_strategies.sector_rs.yf") as mock_yf:
        mock_yf.Ticker.return_value = mock_ticker
        context._etf, context._fallback = get_sector_etf(symbol)


@then("the ETF is \"{expected}\"")
def step_assert_etf(context, expected):
    assert context._etf == expected, (
        f"expected ETF={expected!r}, got {context._etf!r}"
    )


@then("fallback is {expected}")
def step_assert_fallback(context, expected):
    expected_bool = expected.lower() == "true"
    assert context._fallback == expected_bool, (
        f"expected fallback={expected_bool}, got {context._fallback}"
    )


# ── compute_sector_rs — neutral paths ─────────────────────────────

@given("_price_return is mocked to return None")
def step_mock_price_return_none(context):
    context._price_mock = None


@given("_price_return returns {sym_ret:f} for \"{symbol}\" and {etf_ret:f} for \"{etf}\"")
def step_mock_price_returns(context, sym_ret, symbol, etf_ret, etf):
    context._sym_ret = sym_ret
    context._etf_ret = etf_ret
    context._price_sym = symbol.upper()
    context._price_etf = etf.upper()


@when("I call compute_sector_rs for \"{symbol}\"")
def step_compute_rs_with_mocks(context, symbol):
    if hasattr(context, "_price_mock") and context._price_mock is None:
        with patch("tradepro_strategies.sector_rs._price_return", return_value=None):
            context._rs_result = compute_sector_rs(symbol)
    elif hasattr(context, "_sym_ret"):
        def _fake_price_return(sym, *args, **kwargs):
            sym = sym.upper()
            if sym == context._price_sym:
                return context._sym_ret
            if sym == context._price_etf:
                return context._etf_ret
            return None
        with patch("tradepro_strategies.sector_rs._price_return", side_effect=_fake_price_return):
            context._rs_result = compute_sector_rs(symbol)
    else:
        context._rs_result = compute_sector_rs(symbol)


@then("rs_score is {expected:d}")
def step_assert_rs_score(context, expected):
    assert context._rs_result["rs_score"] == expected, (
        f"expected rs_score={expected}, got {context._rs_result['rs_score']}"
    )


@then("error is not None")
def step_error_not_none(context):
    assert context._rs_result["error"] is not None, (
        f"expected error to be set, got None"
    )


@then("rs_12w_pct is approximately {expected:f}")
def step_rs_12w_pct(context, expected):
    actual = context._rs_result["rs_12w_pct"]
    assert actual is not None
    assert abs(actual - expected) < 0.1, f"expected ≈ {expected}, got {actual}"


@then("symbol_12w_pct is {expected:f}")
def step_sym_pct(context, expected):
    assert context._rs_result["symbol_12w_pct"] == expected


@then("etf_12w_pct is {expected:f}")
def step_etf_pct(context, expected):
    assert context._rs_result["etf_12w_pct"] == expected


@then("the result contains keys: {keys_str}")
def step_result_keys(context, keys_str):
    expected_keys = [k.strip() for k in keys_str.split(",")]
    actual_keys = set(context._rs_result.keys())
    for k in expected_keys:
        assert k in actual_keys, f"key {k!r} missing from result. Have: {sorted(actual_keys)}"
