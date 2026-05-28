"""Steps for fundamentals.feature — pin the 2026-05-09 data-quality
regressions (yield guard, fraction-vs-percent threshold, n_holdings
head-cap detection)."""
from __future__ import annotations

from behave import then, when

from tradepro_strategies.fundamentals import _frac_to_pct, _yield_pct


# ---------------------------------------------------------------------------
# Yield + fraction-vs-percent
# ---------------------------------------------------------------------------


@when("I sanitise yield value {raw}")
def step_yield(context, raw: str):
    """Accept "None" or a numeric string. _yield_pct is the helper
    that gates dividend / distribution / yield-to-maturity fields."""
    if raw == "None":
        context.input = None
    else:
        context.input = float(raw)
    context.result = _yield_pct(context.input)


@when("I convert fraction {raw:g} with _frac_to_pct")
def step_frac(context, raw: float):
    """_frac_to_pct is shared with multi-year returns; tests pin that
    it has NO upper cap so a -50% drawdown isn't nulled out."""
    context.result = _frac_to_pct(raw)


@then("the result is None")
def step_result_none(context):
    assert context.result is None, f"expected None, got {context.result!r}"


@then("the result is {expected:g}")
def step_result_eq(context, expected: float):
    assert context.result is not None, f"expected {expected}, got None"
    assert abs(context.result - expected) < 0.001, (
        f"expected {expected}, got {context.result}"
    )


# ---------------------------------------------------------------------------
# n_holdings head-cap detection
# ---------------------------------------------------------------------------


class _FakeDF:
    """Minimal DataFrame stand-in — only `shape` is read."""
    def __init__(self, rows: int):
        self.shape = (rows, 0)


class _FakeFundsData:
    def __init__(self, rows: int):
        self.equity_holdings = _FakeDF(rows)
        self.asset_classes = None


class _FakeTicker:
    def __init__(self, rows: int):
        self.funds_data = _FakeFundsData(rows)


@when("I check funds_data holdings count of {rows:d}")
def step_holdings_count(context, rows: int):
    """Patch yfinance.Ticker so _funds_data_holdings_count sees a
    known row count without touching the network."""
    import sys
    import types
    from tradepro_strategies import fundamentals as fm

    # Build a fake yfinance module with the Ticker hook.
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda symbol: _FakeTicker(rows)
    saved = sys.modules.get("yfinance")
    sys.modules["yfinance"] = fake_yf
    try:
        context.holdings_result = fm._funds_data_holdings_count("TEST")
    finally:
        if saved is not None:
            sys.modules["yfinance"] = saved
        else:
            sys.modules.pop("yfinance", None)


@then("the holdings count result is None")
def step_holdings_none(context):
    assert context.holdings_result is None, (
        f"expected None, got {context.holdings_result!r}"
    )


@then("the holdings count result is {expected:d}")
def step_holdings_eq(context, expected: int):
    assert context.holdings_result == expected, (
        f"expected {expected}, got {context.holdings_result!r}"
    )
