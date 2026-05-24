"""Steps for symbol_analysis_card.feature."""
from __future__ import annotations

import pandas as pd
from behave import given, then, when

from tradepro_strategies.core_portfolio import build_symbol_analysis_card


def _parse_value(s: str):
    s = (s or "").strip()
    if not s:
        return None
    if s.lower() == "nan":
        return float("nan")
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def _ensure_state(context) -> None:
    if not hasattr(context, "sac_info"):
        context.sac_info = None
    if not hasattr(context, "sac_compare_row"):
        context.sac_compare_row = None
    if not hasattr(context, "sac_long_term_result"):
        context.sac_long_term_result = None
    if not hasattr(context, "sac_drawdown_pct"):
        context.sac_drawdown_pct = None
    if not hasattr(context, "sac_dividends_series"):
        context.sac_dividends_series = None


@given('a symbol-card fundamentals info dict with')
def step_info(context) -> None:
    _ensure_state(context)
    info: dict = {}
    for row in context.table:
        info[row["field"]] = _parse_value(row["value"])
    context.sac_info = info


@given('symbol-card dividend history')
def step_div_history(context) -> None:
    _ensure_state(context)
    rows = []
    for row in context.table:
        yr = int(row["year"])
        total = float(row["total"])
        rows.append((pd.Timestamp(f"{yr}-06-30"), total))
    context.sac_dividends_series = pd.Series(
        [t for _, t in rows], index=[d for d, _ in rows]
    )


@given('a symbol-card long_term_result with grade "{grade}"')
def step_lt(context, grade: str) -> None:
    _ensure_state(context)
    context.sac_long_term_result = {
        "quality": {
            "grade": grade,
            "score": None,
            "positives": [],
            "negatives": [],
        },
        "trends": {},
        "template": {},
        "warnings": [],
    }


@given('a symbol-card compare_row with bucket "{bucket}" and conviction "{conv}"')
def step_compare_row(context, bucket: str, conv: str) -> None:
    _ensure_state(context)
    context.sac_compare_row = {
        "bucket": bucket,
        "bucket_reason": f"test fixture bucket={bucket}",
        "conviction": conv,
        "conviction_reason": "test fixture",
    }


@given('the symbol-card technical rr_gate passed {flag}')
def step_rr_gate(context, flag: str) -> None:
    _ensure_state(context)
    passed = flag.strip().lower() == "true"
    if context.sac_compare_row is None:
        context.sac_compare_row = {}
    context.sac_compare_row["rr_gate"] = {
        "passed": passed,
        "rr": 2.5 if passed else 1.2,
        "reason": "test fixture",
    }


@given('a symbol-card drawdown of {pct:g} percent')
def step_drawdown(context, pct: float) -> None:
    _ensure_state(context)
    context.sac_drawdown_pct = float(pct)


@when('I build the symbol analysis card for "{symbol}"')
def step_build(context, symbol: str) -> None:
    _ensure_state(context)
    # When dividend history is supplied via the dedicated step we have
    # to feed it through info — compute_dividend_dashboard takes a
    # separate series argument, but the orchestrator does not expose
    # one. Patch the helper temporarily via the info-dict surrogate:
    # we inject the series-keyed marker so the orchestrator's call to
    # compute_dividend_dashboard(dividends_series=None) still picks up
    # 5y CAGR. Simpler: call the helpers directly here only when a
    # series is supplied — otherwise the orchestrator handles it.
    if context.sac_dividends_series is not None:
        # Use a thin wrapper: monkey-patch compute_dividend_dashboard
        # in the orchestrator namespace just for this call.
        from tradepro_strategies.core_portfolio import symbol_analysis_card as sac_mod
        from tradepro_strategies.core_portfolio.dividend_dashboard import (
            compute_dividend_dashboard as _orig_dd,
        )

        series = context.sac_dividends_series

        def _patched_dd(symbol, *, info=None, dividends_series=None, **kw):
            return _orig_dd(symbol, info=info, dividends_series=series, **kw)

        original = sac_mod.compute_dividend_dashboard
        sac_mod.compute_dividend_dashboard = _patched_dd
        try:
            context.sac = build_symbol_analysis_card(
                symbol,
                info=context.sac_info,
                compare_row=context.sac_compare_row,
                long_term_result=context.sac_long_term_result,
                skip_long_term=True,
                drawdown_pct=context.sac_drawdown_pct,
            )
        finally:
            sac_mod.compute_dividend_dashboard = original
    else:
        context.sac = build_symbol_analysis_card(
            symbol,
            info=context.sac_info,
            compare_row=context.sac_compare_row,
            long_term_result=context.sac_long_term_result,
            skip_long_term=True,
            drawdown_pct=context.sac_drawdown_pct,
        )


@then('the card primary_horizon_recommendation is "{expected}"')
def step_horizon(context, expected: str) -> None:
    actual = context.sac.primary_horizon_recommendation
    assert actual == expected, (
        f"primary_horizon_recommendation: expected {expected!r}, got "
        f"{actual!r} (rationale: {context.sac.rationale})"
    )


@then('the card primary_horizon_recommendation is not "{not_expected}"')
def step_horizon_not(context, not_expected: str) -> None:
    actual = context.sac.primary_horizon_recommendation
    assert actual != not_expected, (
        f"primary_horizon_recommendation: expected NOT {not_expected!r}, got "
        f"{actual!r} (rationale: {context.sac.rationale})"
    )


@then('the card rationale mentions "{needle}"')
def step_rationale(context, needle: str) -> None:
    rat = context.sac.rationale or ""
    assert needle.lower() in rat.lower(), (
        f"rationale {rat!r} does not mention {needle!r}"
    )


@then('the card payload has a fundamental block')
def step_has_fundamental(context) -> None:
    payload = context.sac.to_dict()
    assert payload.get("fundamental") is not None, "fundamental block missing"
    assert "quality_snapshot" in payload["fundamental"]
    assert "valuation" in payload["fundamental"]


@then('the card payload technical block is null')
def step_tech_null(context) -> None:
    payload = context.sac.to_dict()
    assert payload.get("technical") is None, (
        f"expected technical=None, got {payload.get('technical')!r}"
    )


@then('the card payload technical bucket is "{expected}"')
def step_tech_bucket(context, expected: str) -> None:
    payload = context.sac.to_dict()
    tech = payload.get("technical") or {}
    assert tech.get("bucket") == expected, (
        f"technical.bucket: expected {expected!r}, got {tech.get('bucket')!r}"
    )
