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
    """Row that scores ≥ 78 (HIGH conviction, BUY signal).

    Factor scores with strong_sector_rs + eps_up:
      momentum=10, eps=10, quality=9, sector_rs=9, analyst=8, sentiment=9, valuation=6
      raw = 2.0+2.0+1.35+1.35+1.20+0.90+0.30 = 9.10 → score 91
    """
    return {
        "symbol": symbol,
        "market_state": {
            "momentum_3m_pct": 25.0,   # ≥20 → base=9
            "range_pct": 60.0,         # ≤80 → no WATCH override on BUY
        },
        "cross_sectional_momentum": {
            "rank_pct": 0.85,          # ≥0.75 → peer rank bonus +1
        },
        "fundamentals": {
            "free_cashflow": 5e9,      # positive FCF → base=7; sharpe≥0.8 → 9
            "forward_pe": 18.0,        # 18≤pe<28 → score=6
            "legal_type": "Stock",
        },
        "stats": {
            "sharpe": 0.9,             # ≥0.8 → quality base=9
        },
        "external_consensus": {
            "bullScoreLatest": 8,
            "strongBuy": 5,
            "buy": 3,
            "hold": 2,
            "sell": 0,
            "strongSell": 0,
            "momChange": 1,            # turning bullish → +1
        },
        "sentiment_summary": {
            "mean_sentiment": 0.5,     # ≥0.5 → score=9
            "material_negative_count": 0,
        },
    }


def _neutral_row(symbol: str = "NEUT") -> dict:
    """Row that scores 60-71 (MEDIUM conviction, WATCH signal).

    Factor scores with neutral_sector_rs + eps_flat:
      momentum=7, eps=5, quality=7, sector_rs=5, analyst=7, sentiment=7, valuation=6
      raw = 1.40+1.0+1.05+0.75+1.05+0.70+0.30 = 6.25 → score 62.5
    """
    return {
        "symbol": symbol,
        "market_state": {
            "momentum_3m_pct": 12.0,   # ≥10 → base=7
            "range_pct": 55.0,
        },
        "cross_sectional_momentum": {
            "rank_pct": 0.60,          # no peer rank bonus
        },
        "fundamentals": {
            "free_cashflow": 1e9,      # positive FCF → base=7
            "forward_pe": 22.0,        # 18≤pe<28 → score=6
            "legal_type": "Stock",
        },
        "stats": {
            "sharpe": 0.6,             # ≥0.5 → quality stays base=7
        },
        "external_consensus": {
            "bullScoreLatest": 7,
            "strongBuy": 3,
            "buy": 4,
            "hold": 2,
            "sell": 1,
            "strongSell": 0,
            "momChange": 0,            # no momentum change
        },
        "sentiment_summary": {
            "mean_sentiment": 0.25,    # ≥0.2 → score=7
            "material_negative_count": 0,
        },
    }


def _medium_weak_row(symbol: str = "MEDWK") -> dict:
    """Row that scores 40-54 (HOLD signal, LOW conviction).

    Factor scores with neutral_sector_rs + eps_flat:
      momentum=4, eps=5, quality=4, sector_rs=5, analyst=4, sentiment=3, valuation=4
      raw = 0.80+1.0+0.60+0.75+0.60+0.30+0.20 = 4.25 → score 42.5
    """
    return {
        "symbol": symbol,
        "market_state": {
            "momentum_3m_pct": -1.5,   # ≥-3 → base=5
            "range_pct": 40.0,
        },
        "cross_sectional_momentum": {
            "rank_pct": 0.20,          # ≤0.25 → bonus=-1 → score=4
        },
        "fundamentals": {
            "free_cashflow": -1e8,     # ≤0 → base=2; sharpe≥0.8 → base=4
            "forward_pe": 30.0,        # 28≤pe<45 → score=4
            "legal_type": "Stock",
        },
        "stats": {
            "sharpe": 0.85,            # ≥0.8 offset on negative FCF → base=4
        },
        "external_consensus": {
            "bullScoreLatest": 4,
            "strongBuy": 2,
            "buy": 2,
            "hold": 4,
            "sell": 2,
            "strongSell": 0,
            "momChange": -1,           # turning bearish → -1 → score=4
        },
        "sentiment_summary": {
            "mean_sentiment": -0.15,   # ≥-0.3 → score=3
            "material_negative_count": 0,
        },
    }


def _weak_row(symbol: str = "WEAK") -> dict:
    """Row that scores < 40 (TRIM signal).

    Factor scores with weak_sector_rs + eps_down:
      momentum=0, eps=1, quality=2, sector_rs=2, analyst=2, sentiment=0, valuation=2
      raw = 0+0.20+0.30+0.30+0.30+0+0.10 = 1.20 → score 12
    """
    return {
        "symbol": symbol,
        "market_state": {
            "momentum_3m_pct": -15.0,  # ≤-10 → base=1
            "range_pct": 5.0,
        },
        "cross_sectional_momentum": {
            "rank_pct": 0.10,          # ≤0.25 → bonus=-1 → clamps to 0
        },
        "fundamentals": {
            "free_cashflow": -1e9,     # cash burn → base=2; sharpe too low for offset
            "forward_pe": 55.0,        # ≥45 → score=2
            "legal_type": "Stock",
        },
        "stats": {
            "sharpe": -0.2,            # negative → no quality offset
        },
        "external_consensus": {
            "bullScoreLatest": 2,
            "strongBuy": 1,
            "buy": 1,
            "hold": 3,
            "sell": 3,
            "strongSell": 2,
            "momChange": -2,           # strongly turning bearish → score clamps
        },
        "sentiment_summary": {
            "mean_sentiment": -0.6,    # < -0.3 → score=1; mat_neg≥2 → score-1=0
            "material_negative_count": 3,
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
    # 4-tier bucketing so each expected signal/conviction range uses the
    # correct row factory.  Thresholds match COMPASS signal boundaries:
    #   ≥70 → strong   (score ≈ 91 → BUY / HIGH)
    #   55–69 → neutral (score ≈ 62 → WATCH / MEDIUM)
    #   40–54 → medium-weak (score ≈ 42 → HOLD / LOW)
    #   <40  → weak    (score ≈ 12 → TRIM / LOW)
    if target >= 70:
        context._row = _strong_row("HIGH")
        context._sector_rs = _strong_sector_rs()
        context._eps_rev = _eps_up()
    elif target >= 55:
        context._row = _neutral_row("MID")
        context._sector_rs = _neutral_sector_rs()
        context._eps_rev = _eps_flat()
    elif target >= 40:
        context._row = _medium_weak_row("HOLD")
        context._sector_rs = _neutral_sector_rs()
        context._eps_rev = _eps_flat()
    else:
        context._row = _weak_row("LOW")
        context._sector_rs = _weak_sector_rs()
        context._eps_rev = _eps_down()
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
