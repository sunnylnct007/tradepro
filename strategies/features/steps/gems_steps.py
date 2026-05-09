"""Steps for gems.feature — Phase G gem hunter."""
from __future__ import annotations

import re

from behave import given, then, when

from tradepro_strategies.gems import evaluate_gem


_ROW_PATTERN = re.compile(
    r"(?P<sharpe>[-+]?\d*\.?\d+),\s*"
    r"recovers in (?P<rec_months>\d+)mo,\s*"
    r"(?P<dd>[-+]?\d*\.?\d+)% from 5y peak,\s*"
    r"(?P<rp>\d+)(?:st|nd|rd|th)? pctile,\s*"
    r"(?P<flag>CHEAP|FAIR|EXPENSIVE|cheap|fair|expensive),\s*"
    r"RSI (?P<rsi>\d+),\s*"
    r"(?P<sma>above|below) SMA200,\s*"
    r"sentiment (?P<sent>[-+]?\d*\.?\d+)"
)


# Unique prefix so behave's step matcher doesn't collide with risk_steps'
# "a row with vol …" or swing_steps' "a row with Sharpe …".
@given("a gem-profile row: Sharpe {desc}")
def step_row(context, desc: str):
    m = _ROW_PATTERN.search(desc)
    if not m:
        raise AssertionError(f"could not parse row description: {desc!r}")
    g = m.groupdict()
    context.row = {
        "symbol": "TEST",
        "market_state": {
            "rsi_14": float(g["rsi"]),
            "drawdown_from_peak_pct": float(g["dd"]),
            "range_position_pct": float(g["rp"]),
            "above_sma_200": g["sma"] == "above",
            "last_price": 100.0,
        },
        "stats": {
            "sharpe": float(g["sharpe"]),
            "max_drawdown_recovery_days": int(g["rec_months"]) * 30,
        },
        "sentiment_summary": {
            "mean_sentiment": float(g["sent"]),
        },
        "fundamentals": {"n_holdings": 100, "legal_type": "ETF"},
        "valuation_flag": {"flag": g["flag"].lower(), "basis": f"P/E quartile flag {g['flag'].lower()}"},
        "cross_sectional_momentum": {"zscore": 0.0},
    }


@when("I evaluate it as a gem")
def step_eval(context):
    context.verdict = evaluate_gem(context.row)


@then("it is a gem")
def step_is_gem(context):
    assert context.verdict.is_gem, (
        f"expected gem, got fail. failed_filters={context.verdict.reasons.failed_filters}"
    )


@then("it is NOT a gem")
def step_not_gem(context):
    assert not context.verdict.is_gem, (
        f"expected not-a-gem, got is_gem=True. passing={context.verdict.reasons.all_passing()}"
    )


@then('the passing reasons mention "{snippet}"')
def step_passing_mentions(context, snippet: str):
    found = any(snippet in r for r in context.verdict.reasons.all_passing())
    assert found, f"passing reasons missing {snippet!r}: {context.verdict.reasons.all_passing()}"


@then('the recovery signals mention "{snippet}"')
def step_recovery_mentions(context, snippet: str):
    found = any(snippet in r for r in context.verdict.reasons.recovery_signals)
    assert found, f"recovery signals missing {snippet!r}: {context.verdict.reasons.recovery_signals}"


@then('the failed filters mention "{snippet}"')
def step_failed_mentions(context, snippet: str):
    found = any(snippet in r for r in context.verdict.reasons.failed_filters)
    assert found, f"failed filters missing {snippet!r}: {context.verdict.reasons.failed_filters}"


# ---------------------------------------------------------------------------
# V2 stock fundamentals + sentiment + recovery + risk + exit + banner
# ---------------------------------------------------------------------------

def _baseline_stock_row(**overrides):
    """Pristine stock-shaped row — passes every gate. Tests override
    one field at a time to isolate each rule."""
    row = {
        "symbol": "TEST",
        "market_state": {
            "rsi_14": 38, "drawdown_from_peak_pct": -38,
            "range_position_pct": 18, "above_sma_200": True,
            "momentum_3m_pct": -3.0, "vol_30d_annual_pct": 35,
            "last_price": 80,
        },
        "stats": {"sharpe": 0.85, "max_drawdown_recovery_days": 400},
        "sentiment_summary": {"mean_sentiment": -0.05,
                              "material_negative_count": 0,
                              "very_negative_count": 0},
        "fundamentals": {"n_holdings": 1, "legal_type": "EQUITY",
                          "debt_to_equity": 0.8, "free_cashflow": 5e9},
        "valuation_flag": {"flag": "cheap", "basis": "P/E 19x"},
        "cross_sectional_momentum": {"zscore": 0.4},
        "risk_rating": {"rating": "MEDIUM"},
    }
    for k, v in overrides.items():
        if "." in k:
            top, sub = k.split(".", 1)
            row.setdefault(top, {})
            row[top][sub] = v
        else:
            row[k] = v
    return row


@given("a stock gem-profile row with debt/equity {dte:g} and FCF {fcf:g}{unit:w}")
def step_stock_dte_fcf(context, dte: float, fcf: float, unit: str):
    multiplier = {"B": 1e9, "M": 1e6}.get(unit, 1.0)
    context.row = _baseline_stock_row()
    context.row["fundamentals"]["debt_to_equity"] = dte
    context.row["fundamentals"]["free_cashflow"] = fcf * multiplier


@given("a stock gem-profile row with {n:d} very-negative headline")
def step_stock_very_neg(context, n: int):
    context.row = _baseline_stock_row()
    context.row["sentiment_summary"]["very_negative_count"] = n


@given("a stock gem-profile row with {n:d} material-negative headlines")
def step_stock_mat_neg(context, n: int):
    context.row = _baseline_stock_row()
    context.row["sentiment_summary"]["material_negative_count"] = n


@given("a stock gem-profile row with vol {vol:g}% (would be MEDIUM)")
def step_stock_med_vol(context, vol: float):
    context.row = _baseline_stock_row()
    context.row["market_state"]["vol_30d_annual_pct"] = vol
    context.row["risk_rating"] = {"rating": "MEDIUM"}


@given("a stock gem-profile row with only RSI 38 bouncing (below SMA200, z negative)")
def step_stock_one_signal(context):
    context.row = _baseline_stock_row()
    context.row["market_state"]["above_sma_200"] = False
    context.row["cross_sectional_momentum"] = {"zscore": -0.4}


@then('the forced risk is "{expected}"')
def step_forced_risk(context, expected: str):
    actual = context.verdict.forced_risk
    assert actual == expected, f"forced_risk: expected {expected!r}, got {actual!r}"


@then("the position cap is {expected:f}")
def step_position_cap(context, expected: float):
    actual = context.verdict.position_cap_pct
    assert abs(actual - expected) < 0.001, f"position_cap_pct: expected {expected}, got {actual}"


# ---- Exit framework ----

@given("a recovered gem position with RSI {rsi:g}, above SMA200, drawdown {dd:g}%")
def step_exit_recovered(context, rsi: float, dd: float):
    from tradepro_strategies.gems import evaluate_gem_exit
    context._evaluate_exit = evaluate_gem_exit
    context.row = _baseline_stock_row()
    context.row["market_state"]["rsi_14"] = rsi
    context.row["market_state"]["above_sma_200"] = True
    context.row["market_state"]["drawdown_from_peak_pct"] = dd


@given("a gem position with sentiment {sent:g}")
def step_exit_sentiment(context, sent: float):
    from tradepro_strategies.gems import evaluate_gem_exit
    context._evaluate_exit = evaluate_gem_exit
    context.row = _baseline_stock_row()
    context.row["sentiment_summary"]["mean_sentiment"] = sent


@given("a gem position with RSI {rsi:g}, sentiment {sent:g}, debt/equity {dte:g}")
def step_exit_hold(context, rsi: float, sent: float, dte: float):
    from tradepro_strategies.gems import evaluate_gem_exit
    context._evaluate_exit = evaluate_gem_exit
    context.row = _baseline_stock_row()
    context.row["market_state"]["rsi_14"] = rsi
    context.row["sentiment_summary"]["mean_sentiment"] = sent
    context.row["fundamentals"]["debt_to_equity"] = dte


@when("I evaluate the gem exit")
def step_eval_exit(context):
    context.exit_verdict = context._evaluate_exit(context.row)


@then('the exit action is "{expected}"')
def step_exit_action(context, expected: str):
    actual = context.exit_verdict.action
    assert actual == expected, (
        f"exit action: expected {expected!r}, got {actual!r} "
        f"(reasons={context.exit_verdict.reasons})"
    )


# ---- Sector concentration banner ----

@given("{n:d} gem rows where {m:d} are in the energy sector")
def step_sector_concentration(context, n: int, m: int):
    rows = []
    for i in range(m):
        rows.append({
            "symbol": f"ENERGY{i}",
            "fundamentals": {"sector": "energy"},
        })
    for i in range(n - m):
        rows.append({
            "symbol": f"OTHER{i}",
            "fundamentals": {"sector": f"sector_{i}"},
        })
    context.gem_rows = rows


@given("{n:d} gem rows with one in each of {m:d} different sectors")
def step_sector_spread(context, n: int, m: int):
    context.gem_rows = [
        {"symbol": f"S{i}", "fundamentals": {"sector": f"sector_{i}"}}
        for i in range(n)
    ]


@when("I check sector concentration")
def step_check_concentration(context):
    from tradepro_strategies.gems import sector_concentration_banner
    context.banner = sector_concentration_banner(context.gem_rows)


@then('the banner mentions "{snippet}"')
def step_banner_mentions(context, snippet: str):
    assert context.banner is not None, "expected a banner, got None"
    assert snippet in context.banner, f"banner missing {snippet!r}: {context.banner!r}"


@then("no sector banner fires")
def step_no_banner(context):
    assert context.banner is None, f"expected no banner, got: {context.banner!r}"
