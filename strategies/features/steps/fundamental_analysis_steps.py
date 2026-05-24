"""Steps for fundamental_analysis.feature.

All scenarios run without any network calls.  yfinance is replaced by
stub DataFrames / mock Tickers injected via the _ticker_factory seam.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
from behave import given, then, when

from tradepro_strategies.fundamental_analysis import (
    KNOWN_PEERS,
    SECTOR_TEMPLATES,
    _template_key,
    analyse_long_term,
    compute_cagr,
    compute_fcf,
    compute_fcf_conversion,
    compute_financial_trends,
    compute_margin_series,
    margin_trend,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _make_income_df(revenues: list[float],
                    gross_profits: list[float] | None = None,
                    op_incomes: list[float] | None = None,
                    net_incomes: list[float] | None = None) -> pd.DataFrame:
    """Minimal stub income statement in yfinance format.

    Returns a DataFrame with metric names as the index and ISO-date
    strings as columns (newest-first), matching what yfinance produces.
    """
    from datetime import date, timedelta
    n = len(revenues)
    # dates as row-index of the un-transposed frame (newest first)
    dates = [(date.today() - timedelta(days=365 * i)).isoformat() for i in range(n)]
    data: dict[str, list] = {"Total Revenue": revenues}
    if gross_profits is not None:
        data["Gross Profit"] = gross_profits
    if op_incomes is not None:
        data["Operating Income"] = op_incomes
    if net_incomes is not None:
        data["Net Income"] = net_incomes
    # pd.DataFrame(data, index=dates) → shape (n × metrics)
    # .T                              → shape (metrics × n)  ← yfinance format
    return pd.DataFrame(data, index=dates).T


def _make_balance_df(equities: list[float],
                     debts: list[float] | None = None,
                     current_assets: list[float] | None = None,
                     current_liabs: list[float] | None = None) -> pd.DataFrame:
    from datetime import date, timedelta
    n = len(equities)
    dates = [(date.today() - timedelta(days=365 * i)).isoformat() for i in range(n)]
    data: dict[str, list] = {"Common Stock Equity": equities}
    if debts is not None:
        data["Long Term Debt"] = debts
    if current_assets is not None:
        data["Current Assets"] = current_assets
    if current_liabs is not None:
        data["Current Liabilities"] = current_liabs
    return pd.DataFrame(data, index=dates).T


def _make_cashflow_df(op_cf: list[float],
                      capex: list[float] | None = None) -> pd.DataFrame:
    from datetime import date, timedelta
    n = len(op_cf)
    dates = [(date.today() - timedelta(days=365 * i)).isoformat() for i in range(n)]
    data: dict[str, list] = {"Operating Cash Flow": op_cf}
    if capex is not None:
        data["Capital Expenditure"] = capex
    return pd.DataFrame(data, index=dates).T


def _build_ticker_stub(revenues, gross_profits, op_incomes, net_incomes,
                        equities, debts, op_cf, capex,
                        info: dict | None = None) -> MagicMock:
    """Return a MagicMock Ticker whose DataFrame properties are pre-set."""
    ticker = MagicMock()
    ticker.info = info or {
        "sector": "Technology",
        "industry": "Software—Application",
        "forwardPE": 25.0,
        "trailingPE": 30.0,
        "priceToBook": 5.0,
        "returnOnEquity": 0.22,
        "profitMargins": 0.18,
        "marketCap": 2_000_000_000_000,
        "currency": "USD",
    }
    ticker.income_stmt = _make_income_df(revenues, gross_profits,
                                          op_incomes, net_incomes)
    ticker.balance_sheet = _make_balance_df(equities, debts)
    ticker.cashflow = _make_cashflow_df(op_cf, capex)
    return ticker


# ── compute_cagr ───────────────────────────────────────────────────────────

@when("I compute_cagr with values=[{vals}] and years={years:d}")
def step_compute_cagr(context, vals, years):
    values = [float(v.strip()) for v in vals.split(",")]
    context._cagr = compute_cagr(values, years)


@then("cagr_result is approximately {expected:f}")
def step_cagr_approx(context, expected):
    assert context._cagr is not None, "expected a float, got None"
    assert abs(context._cagr - expected) < 0.002, (
        f"expected CAGR ≈ {expected}, got {context._cagr}"
    )


@then("cagr_result is None")
def step_cagr_none(context):
    assert context._cagr is None, f"expected None, got {context._cagr}"


# ── compute_margin_series ──────────────────────────────────────────────────

@when("I call compute_margin_series with numerator=[{num}] denominator=[{den}] max_years={years:d}")
def step_compute_margin_series(context, num, den, years):
    numerator   = [float(v.strip()) for v in num.split(",")]
    denominator = [float(v.strip()) for v in den.split(",")]
    context._margin_series = compute_margin_series(numerator, denominator, years)


@then("margin_series is [{expected}]")
def step_margin_series_exact(context, expected):
    exp_vals = [float(v.strip()) for v in expected.split(",")]
    assert len(context._margin_series) == len(exp_vals), (
        f"expected length {len(exp_vals)}, got {len(context._margin_series)}"
    )
    for i, (a, e) in enumerate(zip(context._margin_series, exp_vals)):
        assert a is not None
        assert abs(a - e) < 0.05, f"index {i}: expected {e}, got {a}"


@then("margin_series has None at index {idx:d}")
def step_margin_series_none_at(context, idx):
    assert context._margin_series[idx] is None, (
        f"expected None at index {idx}, got {context._margin_series[idx]}"
    )


# ── margin_trend ───────────────────────────────────────────────────────────

@when("I call margin_trend with series=[{vals}]")
def step_margin_trend(context, vals):
    series = [
        None if v.strip().lower() == "none" else float(v.strip())
        for v in vals.split(",")
    ]
    context._trend_result = margin_trend(series)


@then("trend_result is \"{expected}\"")
def step_trend_result(context, expected):
    assert context._trend_result == expected, (
        f"expected {expected!r}, got {context._trend_result!r}"
    )


# ── compute_fcf ────────────────────────────────────────────────────────────

@when("I call compute_fcf with op_cashflow=[{ocf}] capex=[{cap}] max_years={years:d}")
def step_compute_fcf(context, ocf, cap, years):
    op_cashflow = [float(v.strip()) for v in ocf.split(",")]
    capex       = [float(v.strip()) for v in cap.split(",")]
    context._fcf_series = compute_fcf(op_cashflow, capex, years)


@then("fcf_series first value is {expected}")
def step_fcf_first(context, expected):
    exp = None if expected.strip().lower() == "none" else float(expected)
    val = context._fcf_series[0] if context._fcf_series else None
    if exp is None:
        assert val is None, f"expected None, got {val}"
    else:
        assert val is not None, f"expected {exp}, got None"
        assert abs(val - exp) < 0.5, f"expected {exp}, got {val}"


# ── compute_fcf_conversion ─────────────────────────────────────────────────

@when("I call compute_fcf_conversion with fcf=[{fcf}] net_income=[{ni}] max_years={years:d}")
def step_compute_fcf_conversion(context, fcf, ni, years):
    fcf_list = [float(v.strip()) for v in fcf.split(",")]
    ni_list  = [float(v.strip()) for v in ni.split(",")]
    context._fcf_conversion = compute_fcf_conversion(fcf_list, ni_list, years)


@then("fcf_conversion first value is {expected}")
def step_fcf_conversion_first(context, expected):
    exp = None if expected.strip().lower() == "none" else float(expected)
    val = context._fcf_conversion[0] if context._fcf_conversion else None
    if exp is None:
        assert val is None, f"expected None, got {val}"
    else:
        assert val is not None, f"expected {exp}, got None"
        assert abs(val - exp) < 1.0, f"expected {exp}, got {val}"


# ── compute_financial_trends ───────────────────────────────────────────────

@given("stub financials for a tech company with 4 years of growth")
def step_stub_tech_financials(context):
    # Revenues growing ~15% yoy: 150, 130, 113, 98 (newest-first)
    context._income   = _make_income_df(
        revenues     = [150_000, 130_000, 113_000, 98_000],
        gross_profits= [75_000,  63_000,  53_000,  45_000],
        op_incomes   = [30_000,  24_000,  19_000,  15_000],
        net_incomes  = [22_000,  18_000,  14_000,  11_000],
    )
    context._balance  = _make_balance_df(
        equities      = [90_000, 75_000, 60_000, 50_000],
        debts         = [20_000, 22_000, 25_000, 28_000],
    )
    context._cashflow = _make_cashflow_df(
        op_cf = [28_000, 23_000, 18_000, 14_000],
        capex = [-5_000, -4_000, -3_500, -3_000],
    )


@given("empty stub financials")
def step_stub_empty_financials(context):
    import pandas as pd
    context._income   = pd.DataFrame()
    context._balance  = pd.DataFrame()
    context._cashflow = pd.DataFrame()


@when("I call compute_financial_trends")
def step_call_compute_trends(context):
    context._trends = compute_financial_trends(
        context._income, context._balance, context._cashflow
    )


@then("revenue_cagr_3y is positive")
def step_cagr_positive(context):
    v = context._trends.get("revenue_cagr_3y")
    assert v is not None and v > 0, f"expected positive revenue_cagr_3y, got {v}"


@then("op_margin_pct_latest is positive")
def step_op_margin_positive(context):
    v = context._trends.get("op_margin_pct_latest")
    assert v is not None and v > 0, f"expected positive op_margin_pct_latest, got {v}"


@then("fcf_conversion_latest is not None")
def step_fcf_conv_not_none(context):
    assert context._trends.get("fcf_conversion_latest") is not None


@then("debt_equity_latest is not None")
def step_de_not_none(context):
    assert context._trends.get("debt_equity_latest") is not None


@then("revenue_cagr_3y is None")
def step_cagr_none_trends(context):
    assert context._trends.get("revenue_cagr_3y") is None


@then("op_margin_pct_latest is None")
def step_op_margin_none(context):
    assert context._trends.get("op_margin_pct_latest") is None


# ── _template_key ──────────────────────────────────────────────────────────

@when("I call _template_key with sector=\"{sector}\" industry=\"{industry}\"")
def step_template_key(context, sector, industry):
    # "(empty)" sentinel used in Scenario Outline for blank cells
    s = "" if sector  == "(empty)" else sector
    i = "" if industry == "(empty)" else industry
    context._tpl_key = _template_key(s, i)


@then("template_key_result is \"{expected}\"")
def step_template_key_result(context, expected):
    assert context._tpl_key == expected, (
        f"expected {expected!r}, got {context._tpl_key!r}"
    )


# ── analyse_long_term ──────────────────────────────────────────────────────

def _default_ticker_stub(sym: str) -> MagicMock:
    """Generic 4-year tech stub for any symbol."""
    return _build_ticker_stub(
        revenues     = [150_000, 130_000, 113_000, 98_000],
        gross_profits= [75_000,  63_000,  53_000,  45_000],
        op_incomes   = [30_000,  24_000,  19_000,  15_000],
        net_incomes  = [22_000,  18_000,  14_000,  11_000],
        equities     = [90_000, 75_000, 60_000, 50_000],
        debts        = [20_000, 22_000, 25_000, 28_000],
        op_cf        = [28_000, 23_000, 18_000, 14_000],
        capex        = [-5_000, -4_000, -3_500, -3_000],
    )


@given("a ticker stub for \"{symbol}\" with 4 years of income stmt, balance sheet, and cashflow")
def step_ticker_stub(context, symbol):
    if not hasattr(context, "_stubs"):
        context._stubs = {}
    context._stubs[symbol.upper()] = _default_ticker_stub(symbol.upper())
    context._primary_symbol = symbol.upper()


@given("peer stubs for [\"{p1}\", \"{p2}\", \"{p3}\"] each with 4 years of data")
def step_peer_stubs(context, p1, p2, p3):
    if not hasattr(context, "_stubs"):
        context._stubs = {}
    for psym in (p1, p2, p3):
        context._stubs[psym.upper()] = _default_ticker_stub(psym.upper())


@given("a ticker stub for \"{symbol}\" with empty financials")
def step_ticker_stub_empty(context, symbol):
    import pandas as pd
    ticker = MagicMock()
    ticker.info = {}
    ticker.income_stmt  = pd.DataFrame()
    ticker.balance_sheet= pd.DataFrame()
    ticker.cashflow     = pd.DataFrame()
    if not hasattr(context, "_stubs"):
        context._stubs = {}
    context._stubs[symbol.upper()] = ticker
    context._primary_symbol = symbol.upper()


@given("a ticker stub with revenue_cagr={cagr:f} roe={roe:f} fcf_conversion={fcf_conv:f} de={de:f}")
def step_ticker_stub_quality(context, cagr, roe, fcf_conv, de):
    """Build a ticker stub that will produce the requested quality metrics.

    For STRONG (cagr>0): we deliberately make operating margins EXPAND
    over the 4-year window (25% newest → 20% oldest) so the quality
    verdict adds the +8 EXPANDING bonus and reaches grade A.
    """
    rev0    = 100_000.0
    rev3    = rev0 / ((1 + cagr) ** 3) if cagr != -1 else rev0 * 4
    revenues = [round(rev0), round(rev0 * 0.87), round(rev0 * 0.75), round(rev3)]

    equity0  = 50_000.0
    net_inc0 = equity0 * roe / 100
    op_cf0   = net_inc0 * (fcf_conv / 100) * 1.1
    capex0   = max(op_cf0 - net_inc0 * (fcf_conv / 100), 0)
    debt0    = equity0 * de

    # Expanding op margins: 25% newest → 20% oldest (visible trend for STRONG)
    # Flat margins for WEAK (same percent, so STABLE/COMPRESSING with falling rev)
    if cagr > 0:
        op_margin_factors = [0.25, 0.24, 0.22, 0.20]   # expanding
    else:
        op_margin_factors = [0.10, 0.10, 0.10, 0.10]   # flat (STABLE)
    op_incomes = [round(revenues[i] * op_margin_factors[i]) for i in range(4)]

    gross0 = net_inc0 * 2.5
    ticker = _build_ticker_stub(
        revenues     = revenues,
        gross_profits= [round(gross0)] * 4,
        op_incomes   = op_incomes,
        net_incomes  = [round(net_inc0)] * 4,
        equities     = [round(equity0)] * 4,
        debts        = [round(debt0)] * 4,
        op_cf        = [round(op_cf0)] * 4,
        capex        = [-round(max(capex0, 1))] * 4,
        info         = {
            "sector": "Technology",
            "industry": "Software",
            "forwardPE": 14.0 if cagr > 0.1 else 45.0,
        },
    )
    if not hasattr(context, "_stubs"):
        context._stubs = {}
    context._stubs["STRONG"] = ticker
    context._stubs["WEAK"]   = ticker


@when("I call analyse_long_term for \"{symbol}\"")
def step_call_analyse(context, symbol):
    stubs = getattr(context, "_stubs", {})

    def factory(sym: str):
        return stubs.get(sym.upper(), _default_ticker_stub(sym))

    context._result = analyse_long_term(
        symbol,
        years=4,
        include_peers=True,
        _ticker_factory=factory,
    )


@when("I call analyse_long_term for \"{symbol}\" with include_peers=False")
def step_call_analyse_no_peers(context, symbol):
    stubs = getattr(context, "_stubs", {})

    def factory(sym: str):
        return stubs.get(sym.upper(), _default_ticker_stub(sym))

    context._result = analyse_long_term(
        symbol,
        years=4,
        include_peers=False,
        _ticker_factory=factory,
    )


@then("the result has ok=True")
def step_result_ok(context):
    assert context._result.get("ok") is True, (
        f"expected ok=True, got: {context._result.get('ok')!r}, "
        f"error={context._result.get('error')!r}"
    )


@then("the result contains key \"{key}\"")
def step_result_has_key(context, key):
    assert key in context._result, (
        f"key {key!r} missing from result. Keys: {sorted(context._result.keys())}"
    )


@then("quality grade is \"{expected}\"")
def step_quality_grade(context, expected):
    grade = context._result.get("quality", {}).get("grade")
    assert grade == expected, f"expected grade={expected!r}, got {grade!r}"


@then("quality grade is one of [{options}]")
def step_quality_grade_one_of(context, options):
    allowed = [o.strip().strip('"').strip("'") for o in options.split(",")]
    grade = context._result.get("quality", {}).get("grade")
    assert grade in allowed, f"expected grade in {allowed}, got {grade!r}"


@then("positives list is non-empty")
def step_positives_non_empty(context):
    positives = context._result.get("quality", {}).get("positives", [])
    assert len(positives) > 0, "expected at least one positive signal"


@then("negatives list is non-empty")
def step_negatives_non_empty(context):
    negatives = context._result.get("quality", {}).get("negatives", [])
    assert len(negatives) > 0, "expected at least one negative signal"


@then("warnings list is non-empty")
def step_warnings_non_empty(context):
    warnings = context._result.get("warnings", [])
    assert len(warnings) > 0, "expected at least one warning"


@then("peers list has at least {n:d} entry")
def step_peers_non_empty(context, n):
    peers = context._result.get("peers", [])
    assert len(peers) >= n, f"expected >= {n} peers, got {len(peers)}"


@then("peers list is empty")
def step_peers_empty(context):
    peers = context._result.get("peers", [])
    assert len(peers) == 0, f"expected empty peers, got {len(peers)}"


# ── KNOWN_PEERS / SECTOR_TEMPLATES ─────────────────────────────────────────

@then("KNOWN_PEERS contains key \"{key}\"")
def step_known_peers_has_key(context, key):
    assert key in KNOWN_PEERS, f"KNOWN_PEERS missing key {key!r}"


@then("KNOWN_PEERS[\"{key}\"] includes \"{value}\"")
def step_known_peers_includes(context, key, value):
    assert key in KNOWN_PEERS, f"KNOWN_PEERS missing key {key!r}"
    assert value in KNOWN_PEERS[key], (
        f"expected {value!r} in KNOWN_PEERS[{key!r}], got {KNOWN_PEERS[key]}"
    )


@then("SECTOR_TEMPLATES[\"{key}\"] has non-empty yfinance_gaps")
def step_template_has_gaps(context, key):
    assert key in SECTOR_TEMPLATES, f"SECTOR_TEMPLATES missing key {key!r}"
    gaps = SECTOR_TEMPLATES[key].get("yfinance_gaps", [])
    assert len(gaps) > 0, f"expected non-empty yfinance_gaps for {key!r}"


@then("SECTOR_TEMPLATES[\"{key}\"][\"yfinance_gaps\"] mentions \"{substring}\"")
def step_template_gaps_mention(context, key, substring):
    gaps = SECTOR_TEMPLATES.get(key, {}).get("yfinance_gaps", [])
    combined = " ".join(gaps)
    assert substring in combined, (
        f"expected {substring!r} to appear in yfinance_gaps for {key!r}. "
        f"Got: {gaps}"
    )
