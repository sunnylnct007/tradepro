"""Steps for dispersion.feature — pin the macro_proxies basket and
the get_returns reference-price math."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from behave import given, then, when

from tradepro_strategies.mcp.tools import _ref_price
from tradepro_strategies.watchlists import resolve as resolve_watchlist


@when("I resolve the {name} watchlist")
def step_resolve(context, name: str):
    context.symbols = resolve_watchlist(name)


@then("it includes risk-on equity proxies (SPY, QQQ, EFA, EEM)")
def step_risk_on(context):
    for sym in ("SPY", "QQQ", "EFA", "EEM"):
        assert sym in context.symbols, f"missing risk-on proxy {sym}"


@then("it includes risk-off proxies (TLT, AGG, GLD)")
def step_risk_off(context):
    for sym in ("TLT", "AGG", "GLD"):
        assert sym in context.symbols, f"missing risk-off proxy {sym}"


@then("it includes a commodity proxy (USO)")
def step_commodity(context):
    assert "USO" in context.symbols


@then("it includes a sector / event proxy (XLE, ITA)")
def step_sector(context):
    for sym in ("XLE", "ITA"):
        assert sym in context.symbols


@then("it includes a volatility proxy (VIXY)")
def step_vol(context):
    assert "VIXY" in context.symbols


@then("it contains {symbol}")
def step_contains(context, symbol: str):
    assert symbol in context.symbols, f"{symbol} missing from watchlist"


@then("every symbol has a macro_axis label")
def step_every_has_axis(context):
    from tradepro_strategies.watchlists import macro_axis_for
    orphans = [s for s in context.symbols if macro_axis_for(s) is None]
    assert not orphans, (
        f"symbols in etf_macro_proxies with no axis label: {orphans} — "
        f"add them to MACRO_PROXIES_BY_AXIS"
    )


@then("every axis member appears in the watchlist")
def step_axis_members_in_watchlist(context):
    from tradepro_strategies.watchlists import MACRO_PROXIES_BY_AXIS
    members = {s for syms in MACRO_PROXIES_BY_AXIS.values() for s in syms}
    extras = sorted(members - set(context.symbols))
    assert not extras, (
        f"symbols in MACRO_PROXIES_BY_AXIS but not in etf_macro_proxies: "
        f"{extras} — add them to the watchlist"
    )


@given("a daily price series ending on a Friday")
def step_friday_series(context):
    # Build 30 weekday-only bars ending on a Friday.
    end = pd.Timestamp("2026-04-24")  # Friday
    assert end.weekday() == 4
    idx = pd.bdate_range(end=end, periods=30)
    series = pd.Series([100.0 + i for i in range(len(idx))], index=idx)
    context.series = series
    context.last_dt = idx[-1]


@when("I look up the 1d reference price for the basket")
def step_lookup(context):
    context.ref_1d = _ref_price(context.series, "1d", context.last_dt)
    context.ref_5d = _ref_price(context.series, "5d", context.last_dt)


@then("the reference price is the prior trading day's close")
def step_prior_trading_day(context):
    expected = float(context.series.iloc[-2])  # Thursday's close
    assert context.ref_1d == expected, f"expected {expected}, got {context.ref_1d}"


@then("the lookup never returns a NaN for a present series")
def step_no_nan(context):
    assert context.ref_1d is not None and context.ref_1d == context.ref_1d
    assert context.ref_5d is not None and context.ref_5d == context.ref_5d
