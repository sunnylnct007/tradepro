"""Steps for paper_quant_strategies.feature.

All scenarios are synthetic: no Yahoo, no T212, no broker connection.
Strategies receive an injected `_data_fn` for daily history, and FX
strategies are driven by manually-constructed hourly Bar streams.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.paper.broker_factory import create_router
from tradepro_strategies.paper.brokers.t212 import T212OrderRouter
from tradepro_strategies.paper.overrides import (
    OverrideAction,
    OverrideRegistry,
    StrategyOverride,
)
from tradepro_strategies.paper.signal_bridge import (
    realised_vol_from_closes,
    size_from_vol_target,
)
from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy
from tradepro_strategies.paper.strategies.ichimoku_fx_mr import (
    IchimokuFXMeanReversionStrategy,
)
from tradepro_strategies.paper.strategy import Bar, OrderSide, OrderType


# ====================================================================== #
# Shared fixture helpers                                                   #
# ====================================================================== #

def _fresh_registry_path() -> Path:
    return Path(tempfile.mkdtemp(prefix="overrides-")) / "paper_overrides.json"


def _make_bar(
    symbol: str,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
    vol: int = 100_000,
    ts: datetime | None = None,
    timeframe_seconds: int = 3600,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts or datetime.now(timezone.utc),
        open=open_ if open_ is not None else close,
        high=high if high is not None else close * 1.001,
        low=low if low is not None else close * 0.999,
        close=close,
        volume=vol,
        timeframe_seconds=timeframe_seconds,
    )


def _make_daily_df(n: int = 300, trend: str = "up", seed: int = 42) -> pd.DataFrame:
    """Synthetic daily OHLCV with a deterministic random walk."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    if trend == "up":
        close = 100.0 + np.cumsum(rng.normal(0.4, 0.8, n))
    elif trend == "down":
        close = 250.0 - np.cumsum(rng.normal(0.4, 0.8, n))
    else:
        close = 100.0 + rng.normal(0, 1.0, n).cumsum() * 0.1
    close = np.maximum(close, 1.0)
    high = close * (1.0 + rng.uniform(0.001, 0.01, n))
    low = close * (1.0 - rng.uniform(0.001, 0.01, n))
    return pd.DataFrame(
        {"high": high, "low": low, "close": close,
         "adj_close": close, "volume": 1_000_000},
        index=dates,
    )


# ====================================================================== #
# Section 1: OverrideRegistry                                              #
# ====================================================================== #

@given("a fresh OverrideRegistry")
def step_fresh_registry(context) -> None:
    context.registry = OverrideRegistry(_fresh_registry_path())


@when('I apply a PAUSE override for "{name}"')
def step_apply_pause(context, name: str) -> None:
    context.registry.apply(
        StrategyOverride(strategy_name=name, action=OverrideAction.PAUSE)
    )


@when('I apply a RESUME override for "{name}"')
def step_apply_resume(context, name: str) -> None:
    context.registry.apply(
        StrategyOverride(strategy_name=name, action=OverrideAction.RESUME)
    )


@when('I apply a PRICE_OVERRIDE for "{name}" symbol "{sym}" price {price:f}')
def step_apply_price(context, name: str, sym: str, price: float) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=name,
        action=OverrideAction.PRICE_OVERRIDE,
        symbol=sym,
        params={"price": price},
    ))


@when('I apply a SIZE_OVERRIDE for "{name}" symbol "{sym}" quantity {qty:d}')
def step_apply_size(context, name: str, sym: str, qty: int) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=name,
        action=OverrideAction.SIZE_OVERRIDE,
        symbol=sym,
        params={"quantity": qty},
    ))


@when('I apply a VETO_ORDER for "{name}" symbol "{sym}"')
def step_apply_veto(context, name: str, sym: str) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=name,
        action=OverrideAction.VETO_ORDER,
        symbol=sym,
    ))


@when('I apply a FORCE_CLOSE for "{name}" symbol "{sym}"')
def step_apply_fc(context, name: str, sym: str) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=name,
        action=OverrideAction.FORCE_CLOSE,
        symbol=sym,
    ))


@when('I clear overrides for "{name}"')
def step_clear(context, name: str) -> None:
    context.registry.clear(name)


@then('is_paused("{name}") returns {expected}')
def step_check_paused(context, name: str, expected: str) -> None:
    want = (expected.strip() == "True")
    assert context.registry.is_paused(name) is want, (
        f"is_paused({name!r}) expected {want}, got {context.registry.is_paused(name)}"
    )


@then('get_price_override "{name}" "{sym}" returns {expected}')
def step_check_price(context, name: str, sym: str, expected: str) -> None:
    actual = context.registry.get_price_override(name, sym)
    if expected.strip() == "None":
        assert actual is None, f"expected None, got {actual}"
    else:
        assert actual == float(expected), f"expected {expected}, got {actual}"


@then('get_size_override "{name}" "{sym}" returns {expected}')
def step_check_size(context, name: str, sym: str, expected: str) -> None:
    actual = context.registry.get_size_override(name, sym)
    if expected.strip() == "None":
        assert actual is None, f"expected None, got {actual}"
    else:
        assert actual == int(expected), f"expected {expected}, got {actual}"


@then('consume_veto "{name}" "{sym}" returns {expected}')
def step_consume_veto(context, name: str, sym: str, expected: str) -> None:
    actual = context.registry.consume_veto(name, sym)
    want = (expected.strip() == "True")
    assert actual is want, f"expected {want}, got {actual}"


@then('consume_force_close "{name}" "{sym}" returns {expected}')
def step_consume_fc(context, name: str, sym: str, expected: str) -> None:
    actual = context.registry.consume_force_close(name, sym)
    want = (expected.strip() == "True")
    assert actual is want, f"expected {want}, got {actual}"


# ====================================================================== #
# Section 2: signal_bridge helpers                                         #
# ====================================================================== #

@given(
    "a price of {price:d}, capital {capital:d}, target_vol {tv:f}, "
    "realised_vol {rv} , max_leverage {ml:f}"
)
def _step_size_inputs_raw(context, price, capital, tv, rv, ml):
    # Placeholder — overridden by the variant below (behave picks one).
    pass


@given(
    "a price of {price:d}, capital {capital:d}, target_vol {tv:f}, "
    "realised_vol {rv}, max_leverage {ml:f}"
)
def step_size_inputs(context, price, capital, tv, rv, ml) -> None:
    rv_s = rv.strip()
    realised: float | None
    if rv_s == "None":
        realised = None
    else:
        realised = float(rv_s)
    context.size_inputs = {
        "price": float(price),
        "capital": float(capital),
        "target_vol": float(tv),
        "realised_vol": realised,
        "max_leverage": float(ml),
    }


@when("I call size_from_vol_target")
def step_call_size(context) -> None:
    s = context.size_inputs
    context.qty = size_from_vol_target(
        price=s["price"],
        capital=s["capital"],
        target_vol=s["target_vol"],
        realised_vol=s["realised_vol"],
        max_leverage=s["max_leverage"],
    )


@then("the quantity equals {expected:d}")
def step_check_qty(context, expected: int) -> None:
    assert context.qty == expected, f"expected {expected}, got {context.qty}"


@given("a synthetic closes series of {n:d} bars with 1% daily noise")
def step_synthetic_closes(context, n: int) -> None:
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0, 0.01, n)  # ~1% daily stdev → ~15.8% annualised
    prices = 100.0 * np.exp(np.cumsum(rets))
    context.closes = prices.tolist()


@when("I call realised_vol_from_closes")
def step_call_realised(context) -> None:
    context.realised = realised_vol_from_closes(context.closes)


@then("the realised vol is between {lo:f} and {hi:f}")
def step_check_realised(context, lo: float, hi: float) -> None:
    assert context.realised is not None, "realised vol was None"
    assert lo <= context.realised <= hi, (
        f"realised vol {context.realised:.4f} not in [{lo}, {hi}]"
    )


# ====================================================================== #
# Section 3: BrokerFactory                                                 #
# ====================================================================== #

@when('I call create_router with broker "{broker}"')
def step_call_factory(context, broker: str) -> None:
    try:
        context.router = create_router(broker)
        context.factory_error = None
    except ValueError as e:
        context.router = None
        context.factory_error = e


@then("the result is a T212OrderRouter")
def step_check_t212(context) -> None:
    assert isinstance(context.router, T212OrderRouter), (
        f"expected T212OrderRouter, got {type(context.router)}"
    )


@then("create_router raises a ValueError")
def step_check_value_error(context) -> None:
    assert isinstance(context.factory_error, ValueError), (
        f"expected ValueError, got {context.factory_error}"
    )


# ====================================================================== #
# Section 4: IchimokuEquityStrategy                                        #
# ====================================================================== #

def _build_equity_strategy(
    symbol: str,
    trend: str,
    seed: int = 42,
) -> tuple[IchimokuEquityStrategy, OverrideRegistry]:
    df_sym = _make_daily_df(trend=trend, seed=seed)
    # SPY uptrend so the regime gate is GREEN.
    df_spy = _make_daily_df(trend="up", seed=11)
    dfs = {symbol: df_sym, "SPY": df_spy}

    def data_fn(s: str) -> pd.DataFrame | None:
        return dfs.get(s)

    reg = OverrideRegistry(_fresh_registry_path())
    strat = IchimokuEquityStrategy(
        strategy_id=f"ie_{symbol.lower()}",
        params={
            "symbols": [symbol],
            "capital_usd": 20_000.0,
            "sleeve_size": 1,
            "_data_fn": data_fn,
            "_override_registry": reg,
        },
    )
    strat.on_session_start(datetime.now(timezone.utc))
    return strat, reg


@given('an IchimokuEquityStrategy bound to symbol "{sym}" with an uptrending feed')
def step_strat_uptrend(context, sym: str) -> None:
    context.strat, context.registry = _build_equity_strategy(sym, "up")
    context.symbol = sym


@given('an IchimokuEquityStrategy bound to symbol "{sym}" with a downtrending feed')
def step_strat_downtrend(context, sym: str) -> None:
    context.strat, context.registry = _build_equity_strategy(sym, "down")
    context.symbol = sym


@given('the strategy already holds {n:d} shares of "{sym}"')
def step_strat_seed_position(context, n: int, sym: str) -> None:
    context.strat._positions[sym] = n


@when("I pause the strategy")
def step_pause_strategy(context) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.strat.strategy_id,
        action=OverrideAction.PAUSE,
    ))


@when('I apply a VETO_ORDER for "{sym}" on the strategy')
def step_apply_veto_strat(context, sym: str) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.strat.strategy_id,
        action=OverrideAction.VETO_ORDER,
        symbol=sym,
    ))


@when('I apply a PRICE_OVERRIDE for "{sym}" price {price:f} on the strategy')
def step_apply_price_strat(context, sym: str, price: float) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.strat.strategy_id,
        action=OverrideAction.PRICE_OVERRIDE,
        symbol=sym,
        params={"price": price},
    ))


@when('I apply a SIZE_OVERRIDE for "{sym}" quantity {qty:d} on the strategy')
def step_apply_size_strat(context, sym: str, qty: int) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.strat.strategy_id,
        action=OverrideAction.SIZE_OVERRIDE,
        symbol=sym,
        params={"quantity": qty},
    ))


@when('I apply a FORCE_CLOSE for "{sym}" on the strategy')
def step_apply_fc_strat(context, sym: str) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.strat.strategy_id,
        action=OverrideAction.FORCE_CLOSE,
        symbol=sym,
    ))


@when('I send one daily bar for "{sym}"')
def step_send_one_bar(context, sym: str) -> None:
    bar = _make_bar(sym, close=250.0, timeframe_seconds=86400)
    context.orders = context.strat.on_bar(bar)


@when('I send another daily bar for "{sym}"')
def step_send_another_bar(context, sym: str) -> None:
    bar = _make_bar(sym, close=251.0, timeframe_seconds=86400)
    context.second_orders = context.strat.on_bar(bar)


@then("no orders are emitted")
def step_no_orders(context) -> None:
    assert context.orders == [], f"expected no orders, got {context.orders}"


@then("the second bar emits no orders")
def step_no_second_orders(context) -> None:
    assert context.second_orders == [], (
        f"expected no orders, got {context.second_orders}"
    )


@then('a BUY MARKET order is emitted for "{sym}"')
def step_buy_market(context, sym: str) -> None:
    assert len(context.orders) == 1, f"expected 1 order, got {context.orders}"
    o = context.orders[0]
    assert o.symbol == sym, f"symbol {o.symbol} != {sym}"
    assert o.side == OrderSide.BUY, f"side {o.side} != BUY"
    assert o.type == OrderType.MARKET, f"type {o.type} != MARKET"


@then('a SELL MARKET order is emitted for "{sym}"')
def step_sell_market(context, sym: str) -> None:
    assert len(context.orders) == 1, f"expected 1 order, got {context.orders}"
    o = context.orders[0]
    assert o.symbol == sym
    assert o.side == OrderSide.SELL
    assert o.type == OrderType.MARKET


@then('a BUY LIMIT order is emitted for "{sym}" at {price:f}')
def step_buy_limit(context, sym: str, price: float) -> None:
    assert len(context.orders) == 1, f"expected 1 order, got {context.orders}"
    o = context.orders[0]
    assert o.symbol == sym
    assert o.side == OrderSide.BUY
    assert o.type == OrderType.LIMIT
    assert abs((o.limit_price or 0.0) - price) < 1e-9, (
        f"limit_price {o.limit_price} != {price}"
    )


@then('the order tag contains "{needle}"')
def step_tag_contains(context, needle: str) -> None:
    assert len(context.orders) == 1
    assert needle in context.orders[0].tag, (
        f"tag {context.orders[0].tag!r} missing {needle!r}"
    )


@then("the emitted order has quantity {qty:d}")
def step_order_qty(context, qty: int) -> None:
    assert len(context.orders) == 1
    assert context.orders[0].quantity == qty, (
        f"qty {context.orders[0].quantity} != {qty}"
    )


# ====================================================================== #
# Section 5: IchimokuFXMeanReversionStrategy                               #
# ====================================================================== #

def _build_fx_strategy(
    pair: str,
    warmup: int = 200,
    horizons: tuple = (24, 48),
    smooths: tuple = (12, 24),
) -> tuple[IchimokuFXMeanReversionStrategy, OverrideRegistry]:
    reg = OverrideRegistry(_fresh_registry_path())
    strat = IchimokuFXMeanReversionStrategy(
        strategy_id=f"fxmr_{pair.lower()}",
        params={
            "pairs": [pair],
            "capital_usd": 50_000.0,
            "vol_target": 0.10,
            "pos_cap": 3,
            "warmup_bars": warmup,
            "horizons": horizons,
            "smooths": smooths,
            "_override_registry": reg,
        },
    )
    strat.on_session_start(datetime.now(timezone.utc))
    return strat, reg


def _engineered_bearish_break_bars(
    pair: str,
    n_pre: int = 300,
    n_break: int = 60,
) -> list[Bar]:
    """OHLC sequence designed to trigger the FX reversion strategy's LONG branch.

    Phase 1 (n_pre bars): random walk around 1.10 to build cloud/tenkan/kijun
                          baseline.
    Phase 2 (n_break bars): sharp drop below the cloud → close < cloud_bottom
                            AND tenkan < kijun → raw=+1 (bearish break → long fade).
    """
    rng = np.random.default_rng(123)
    bars: list[Bar] = []
    base = 1.10
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Phase 1: noisy plateau.
    for i in range(n_pre):
        c = base + rng.normal(0, 0.0005)
        h = c + abs(rng.normal(0, 0.0003))
        low = c - abs(rng.normal(0, 0.0003))
        bars.append(_make_bar(
            pair, close=c, high=h, low=low, open_=c,
            ts=ts + timedelta(hours=i),
        ))

    # Phase 2: steep drop down (well below any prior low).
    for j in range(n_break):
        c = base - 0.02 - (j * 0.0005)  # accelerating drop
        h = c + 0.0002
        low = c - 0.0005
        bars.append(_make_bar(
            pair, close=c, high=h, low=low, open_=c,
            ts=ts + timedelta(hours=n_pre + j),
        ))

    return bars


@given('an IchimokuFXMeanReversionStrategy bound to pair "{pair}"')
def step_fx_strategy(context, pair: str) -> None:
    context.fx_strat, context.registry = _build_fx_strategy(pair, warmup=200)
    context.pair = pair


@given(
    'an IchimokuFXMeanReversionStrategy bound to pair "{pair}" '
    'with engineered bearish break'
)
def step_fx_strategy_break(context, pair: str) -> None:
    context.fx_strat, context.registry = _build_fx_strategy(
        pair, warmup=200, horizons=(24, 48), smooths=(12, 24)
    )
    context.pair = pair
    context.fx_bars = _engineered_bearish_break_bars(pair, n_pre=300, n_break=60)


@given('the FX strategy already holds {n:d} units of "{pair}"')
def step_fx_seed_position(context, n: int, pair: str) -> None:
    context.fx_strat._fx_positions[pair] = n
    # Skip warmup so the next bar can act.
    context.fx_strat._bar_counts[pair] = 9_999


@when('I feed {n:d} random hourly bars for "{pair}"')
def step_feed_random_bars(context, n: int, pair: str) -> None:
    rng = np.random.default_rng(0)
    all_orders: list = []
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        c = 1.10 + rng.normal(0, 0.001)
        bar = _make_bar(
            pair, close=c, high=c + 0.0005, low=c - 0.0005,
            ts=ts + timedelta(hours=i),
        )
        all_orders.extend(context.fx_strat.on_bar(bar))
    context.fx_orders = all_orders


@when("I drive the strategy to compute its signal")
def step_drive_fx(context) -> None:
    all_orders: list = []
    for bar in context.fx_bars:
        all_orders.extend(context.fx_strat.on_bar(bar))
    context.fx_orders = all_orders


@when("I pause the FX strategy")
def step_pause_fx(context) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.fx_strat.strategy_id,
        action=OverrideAction.PAUSE,
    ))


@when('I apply a FORCE_CLOSE for "{pair}" on the FX strategy')
def step_fc_fx(context, pair: str) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.fx_strat.strategy_id,
        action=OverrideAction.FORCE_CLOSE,
        symbol=pair,
    ))


@when('I apply a SIZE_OVERRIDE for "{pair}" quantity {qty:d} on the FX strategy')
def step_size_fx(context, pair: str, qty: int) -> None:
    context.registry.apply(StrategyOverride(
        strategy_name=context.fx_strat.strategy_id,
        action=OverrideAction.SIZE_OVERRIDE,
        symbol=pair,
        params={"quantity": qty},
    ))


@when('I send one hourly bar for "{pair}"')
def step_send_fx_bar(context, pair: str) -> None:
    bar = _make_bar(pair, close=1.10, ts=datetime(2026, 2, 1, tzinfo=timezone.utc))
    context.fx_orders = context.fx_strat.on_bar(bar)


@then("no FX orders are emitted")
def step_no_fx_orders(context) -> None:
    assert context.fx_orders == [], f"expected no orders, got {context.fx_orders}"


@then('a BUY order is emitted for "{pair}"')
def step_buy_fx(context, pair: str) -> None:
    buys = [o for o in context.fx_orders if o.symbol == pair and o.side == OrderSide.BUY]
    assert buys, (
        f"expected at least one BUY for {pair}; got "
        f"{[(o.symbol, o.side, o.quantity) for o in context.fx_orders]}"
    )
    context.fx_orders = [buys[-1]]  # narrow for downstream tag/quantity checks


@then('a SELL FX order is emitted for "{pair}" with quantity {qty:d}')
def step_sell_fx_qty(context, pair: str, qty: int) -> None:
    sells = [o for o in context.fx_orders if o.symbol == pair and o.side == OrderSide.SELL]
    assert sells, (
        f"expected SELL for {pair}; got "
        f"{[(o.symbol, o.side, o.quantity) for o in context.fx_orders]}"
    )
    assert sells[-1].quantity == qty, (
        f"expected qty {qty}, got {sells[-1].quantity}"
    )


@then("the emitted FX order has quantity {qty:d}")
def step_fx_order_qty(context, qty: int) -> None:
    orders_with_qty = [o for o in context.fx_orders if o.quantity == qty]
    assert orders_with_qty, (
        f"no FX order with qty {qty}; got "
        f"{[(o.symbol, o.side, o.quantity) for o in context.fx_orders]}"
    )
