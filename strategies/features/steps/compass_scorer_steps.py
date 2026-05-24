"""Steps for compass_scorer.feature.

The macro regime is always patched to a fixed mode so tests run without
yfinance network calls. Sector RS and EPS revision inputs are supplied as
pre-built dicts, not fetched live.
"""
from __future__ import annotations

from unittest.mock import patch

from behave import given, then, when

from tradepro_strategies.compass_scorer import compute_compass_score, CompassResult


# ── synthetic row factories ────────────────────────────────────────

def _strong_row(symbol: str = "TEST") -> dict:
    """Row that will score high across most COMPASS factors."""
    return {
        "symbol": symbol,
        "close": 200.0,
        "open": 182.0,           # strong 12w momentum proxy
        "high": 205.0,
        "low": 180.0,
        "volume": 5_000_000,
        "rsi": 52.0,             # not overbought
        "sma_200": 170.0,        # well above 200d
        "pct_52w_range": 0.65,
        "return_12w": 22.0,
        "sentiment_score": 0.6,
        "analyst_score": 0.8,    # strong analyst consensus
        "forward_pe": 18.0,
        "roe": 0.28,
        "debt_to_equity": 0.3,
        "fcf_yield": 0.05,
        # fundamentals sub-dict (optional)
        "fundamentals": {
            "forwardPE": 18.0,
            "returnOnEquity": 0.28,
            "debtToEquity": 0.3,
            "freeCashflow": 5e9,
            "marketCap": 100e9,
        },
    }


def _weak_row(symbol: str = "WEAK") -> dict:
    """Row that will score low across COMPASS factors."""
    return {
        "symbol": symbol,
        "close": 50.0,
        "open": 70.0,            # significant price decline
        "high": 52.0,
        "low": 48.0,
        "volume": 500_000,
        "rsi": 72.0,             # overbought (despite downtrend — contradictory, but score is low)
        "sma_200": 75.0,         # well below 200d
        "pct_52w_range": 0.05,
        "return_12w": -18.0,
        "sentiment_score": -0.7,
        "analyst_score": 0.1,
        "forward_pe": 45.0,
        "roe": 0.03,
        "debt_to_equity": 3.5,
        "fcf_yield": -0.02,
        "fundamentals": {
            "forwardPE": 45.0,
            "returnOnEquity": 0.03,
            "debtToEquity": 350.0,
            "freeCashflow": -1e8,
            "marketCap": 5e9,
        },
    }


def _neutral_row(symbol: str = "NEUT") -> dict:
    """Row that will score near the middle of the COMPASS range."""
    return {
        "symbol": symbol,
        "close": 100.0,
        "open": 99.0,
        "high": 102.0,
        "low": 97.0,
        "volume": 1_000_000,
        "rsi": 50.0,
        "sma_200": 98.0,
        "pct_52w_range": 0.50,
        "return_12w": 2.0,
        "sentiment_score": 0.0,
        "analyst_score": 0.5,
        "forward_pe": 22.0,
        "roe": 0.12,
        "debt_to_equity": 1.0,
        "fcf_yield": 0.02,
        "fundamentals": {
            "forwardPE": 22.0,
            "returnOnEquity": 0.12,
            "debtToEquity": 100.0,
            "freeCashflow": 1e9,
            "marketCap": 50e9,
        },
    }


def _strong_sector_rs() -> dict:
    return {
        "symbol": "TEST", "sector_etf": "XLK", "fallback": False,
        "symbol_12w_pct": 22.0, "etf_12w_pct": 8.0,
        "rs_12w_pct": 14.0, "rs_score": 9, "as_of": "2026-05-24", "error": None,
    }


def _weak_sector_rs() -> dict:
    return {
        "symbol": "WEAK", "sector_etf": "XLK", "fallback": False,
        "symbol_12w_pct": 2.0, "etf_12w_pct": 15.0,
        "rs_12w_pct": -13.0, "rs_score": 2, "as_of": "2026-05-24", "error": None,
    }


def _neutral_sector_rs() -> dict:
    return {
        "symbol": "NEUT", "sector_etf": "XLK", "fallback": False,
        "symbol_12w_pct": 5.0, "etf_12w_pct": 4.5,
        "rs_12w_pct": 0.5, "rs_score": 5, "as_of": "2026-05-24", "error": None,
    }


def _eps_up() -> dict:
    return {
        "symbol": "TEST", "current_estimate": 20.0, "estimate_90d_ago": 16.0,
        "delta_90d": 4.0, "direction": "up", "revision_pct": 25.0,
        "snapshots_count": 12, "as_of": "2026-05-24",
    }


def _eps_down() -> dict:
    return {
        "symbol": "WEAK", "current_estimate": 5.0, "estimate_90d_ago": 7.0,
        "delta_90d": -2.0, "direction": "down", "revision_pct": -28.6,
        "snapshots_count": 12, "as_of": "2026-05-24",
    }


def _eps_flat() -> dict:
    return {
        "symbol": "NEUT", "current_estimate": 10.0, "estimate_90d_ago": 10.0,
        "delta_90d": 0.0, "direction": "flat", "revision_pct": 0.0,
        "snapshots_count": 12, "as_of": "2026-05-24",
    }


# ── Given ──────────────────────────────────────────────────────────

@given("any synthetic row for \"{symbol}\"")
def step_any_row(context, symbol):
    context._row = _neutral_row(symbol)
    context._sector_rs = _neutral_sector_rs()
    context._eps_rev = _eps_flat()


@given("a row engineered to yield COMPASS score of {target:d}")
def step_engineered_row(context, target):
    # Use strong row for high targets, weak for low, neutral for mid
    if target >= 70:
        context._row = _strong_row("HIGH")
        context._sector_rs = _strong_sector_rs()
        context._eps_rev = _eps_up()
    elif target <= 40:
        context._row = _weak_row("LOW")
        context._sector_rs = _weak_sector_rs()
        context._eps_rev = _eps_down()
    else:
        context._row = _neutral_row("MID")
        context._sector_rs = _neutral_sector_rs()
        context._eps_rev = _eps_flat()
    context._target_score = target


@given("a row that scores above 72 (BUY territory)")
def step_buy_territory_row(context):
    context._row = _strong_row("BUYTEST")
    context._sector_rs = _strong_sector_rs()
    context._eps_rev = _eps_up()


@given("macro regime is GREEN (patched)")
def step_regime_green(context):
    context._macro_mode = 1


@given("macro regime is AMBER (patched)")
def step_regime_amber(context):
    context._macro_mode = 2


@given("macro regime is RED (patched)")
def step_regime_red(context):
    context._macro_mode = 3


# ── When ──────────────────────────────────────────────────────────

def _run_compass(context, sector_rs=None, eps_rev=None):
    macro_mode = getattr(context, "_macro_mode", 1)
    row = context._row
    if sector_rs is None:
        sector_rs = getattr(context, "_sector_rs", _neutral_sector_rs())
    if eps_rev is None:
        eps_rev = getattr(context, "_eps_rev", _eps_flat())

    with patch("tradepro_strategies.compass_scorer.macro_regime.get_risk_mode",
               return_value=macro_mode):
        result = compute_compass_score(
            row.get("symbol", "TEST"), row,
            sector_rs_result=sector_rs,
            eps_revision=eps_rev,
        )
    context._compass_result = result


@when("I compute the COMPASS score")
def step_compute_compass(context):
    _run_compass(context)


@when("I compute the COMPASS score with sector_rs_result=None")
def step_compute_no_sector(context):
    _run_compass(context, sector_rs=None)
    context._no_exception = True


@when("I compute the COMPASS score with eps_revision=None")
def step_compute_no_eps(context):
    _run_compass(context, eps_rev=None)
    context._no_exception = True


@when("I compute the COMPASS score with sector_rs_result=None and eps_revision=None")
def step_compute_no_both(context):
    _run_compass(context, sector_rs=None, eps_rev=None)
    context._no_exception = True


@when("call to_dict on the result")
def step_to_dict(context):
    context._result_dict = context._compass_result.to_dict()


# ── Then ──────────────────────────────────────────────────────────

@then("the score is between {lo:d} and {hi:d}")
def step_score_between(context, lo, hi):
    score = context._compass_result.score
    assert lo <= score <= hi, f"expected score in [{lo},{hi}], got {score}"


@then("the score is a number")
def step_score_is_number(context):
    score = context._compass_result.score
    assert isinstance(score, (int, float)) and score == score, (
        f"score is not a valid number: {score!r}"
    )


@then("the signal is \"{expected}\"")
def step_assert_signal(context, expected):
    assert context._compass_result.signal == expected, (
        f"expected signal={expected!r}, got {context._compass_result.signal!r}"
    )


@then("the compass signal is WATCH or HOLD")
def step_signal_watch_or_hold(context):
    sig = context._compass_result.signal
    assert sig in ("WATCH", "HOLD"), f"expected WATCH or HOLD, got {sig!r}"


@then("conviction is \"{expected}\"")
def step_assert_conviction(context, expected):
    assert context._compass_result.conviction == expected, (
        f"expected conviction={expected!r}, got {context._compass_result.conviction!r}"
    )


@then("macro_gated is {expected}")
def step_macro_gated(context, expected):
    expected_bool = expected.lower() == "true"
    assert context._compass_result.macro_gated == expected_bool, (
        f"expected macro_gated={expected_bool}, got {context._compass_result.macro_gated}"
    )


@then("the raw score is still above {threshold:d}")
def step_raw_score_above(context, threshold):
    assert context._compass_result.score > threshold, (
        f"expected score > {threshold}, got {context._compass_result.score}"
    )


@then("no exception is raised")
def step_no_exception(context):
    assert getattr(context, "_no_exception", True), "an exception was raised"


@then("the dict contains key \"{key}\"")
def step_dict_has_key(context, key):
    d = context._result_dict
    assert key in d, f"key {key!r} missing from to_dict(). Keys: {sorted(d.keys())}"


@then("the factors list has exactly {n:d} items")
def step_factors_count(context, n):
    factors = context._compass_result.factors
    assert len(factors) == n, f"expected {n} factors, got {len(factors)}"


@then("each factor dict has keys: {keys_str}")
def step_factor_keys(context, keys_str):
    required = [k.strip() for k in keys_str.split(",")]
    for f in context._compass_result.factors:
        fd = f if isinstance(f, dict) else f.__dict__
        for k in required:
            assert k in fd, f"factor missing key {k!r}: {fd}"


@then("the sum of all factor weights equals 1.0")
def step_weights_sum(context):
    total = sum(
        (f["weight"] if isinstance(f, dict) else f.weight)
        for f in context._compass_result.factors
    )
    assert abs(total - 1.0) < 1e-6, f"weights sum to {total}, expected 1.0"
