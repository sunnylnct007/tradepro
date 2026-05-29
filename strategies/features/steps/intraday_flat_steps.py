"""Steps for intraday_flat.feature.

All scenarios are synthetic — no Yahoo, no IG, no LLM provider. Daily
history is injected via `_data_fn`; the LLM gate is replaced with a
canned-decision stub when a scenario needs it; the epic map is written
to a tmp JSON the strategy loads at construction.

Test invariants worth knowing:
  - We disable the regime filter by default so scenarios don't need
    to construct SPY history every time. Scenarios that DO test the
    regime filter set `use_regime_filter=True` explicitly.
  - We set the entry/flatten windows to wide UTC ranges so bar
    timestamps don't have to mimic real market hours; the time gates
    are themselves tested by explicit PRE-WINDOW / EOD-WINDOW bars.
  - Synthetic data factories use a fixed seed → deterministic runs.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.paper.llm_gate import GateDecision, LLMGateConfig, LLMSignalGate
from tradepro_strategies.paper.risk import RiskLimits
from tradepro_strategies.paper.strategies.intraday_flat import IntradayFlatStrategy
from tradepro_strategies.paper.strategy import Bar, Fill, OrderSide, OrderType


# ====================================================================== #
# Fixtures                                                                 #
# ====================================================================== #


_SESSION_DATE = datetime(2024, 7, 15, tzinfo=timezone.utc)
# In-window bar timestamp: 14:30 UTC = 10:30 ET (well inside entry window).
_IN_WINDOW_TS = datetime(2024, 7, 15, 14, 30, tzinfo=timezone.utc)
# Pre-window bar: 12:00 UTC, before the 13:35 entry-start default.
_PRE_WINDOW_TS = datetime(2024, 7, 15, 12, 0, tzinfo=timezone.utc)
# EOD-window bar: 19:55 UTC, after the 19:50 flatten-start default.
_EOD_WINDOW_TS = datetime(2024, 7, 15, 19, 55, tzinfo=timezone.utc)


def _make_daily_df(
    n: int = 400,
    start_price: float = 100.0,
    drift: float = 0.4,
    noise: float = 0.6,
    direction: str = "up",
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic daily OHLCV with a deterministic random walk."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    if direction == "up":
        close = start_price + np.cumsum(rng.normal(drift, noise, n))
    elif direction == "down":
        close = start_price - np.cumsum(rng.normal(drift, noise, n))
    else:  # "flat"
        close = start_price + rng.normal(0, 0.1, n).cumsum() * 0.05
    close = np.maximum(close, 1.0)
    high = close * (1.0 + rng.uniform(0.001, 0.01, n))
    low = close * (1.0 - rng.uniform(0.001, 0.01, n))
    return pd.DataFrame(
        {"high": high, "low": low, "close": close,
         "adj_close": close, "volume": 1_000_000},
        index=dates,
    )


def _write_epic_map(mapping: dict[str, str | None]) -> Path:
    """Write a tmp ig_epic_map JSON with the given symbol→epic dict."""
    payload: dict[str, Any] = {
        "_comment": "test fixture",
    }
    for sym, epic in mapping.items():
        payload[sym] = {
            "epic": epic,
            "currency": "USD",
            "size_unit": "shares",
        }
    path = Path(tempfile.mkdtemp(prefix="igmap-")) / "ig_epic_map.json"
    path.write_text(json.dumps(payload))
    return path


def _build_strategy(
    context,
    candidates: list[str],
    *,
    map_mapping: dict[str, str | None] | None = None,
    data_overrides: dict[str, pd.DataFrame] | None = None,
    use_regime_filter: bool = False,
    regime_symbol_df: pd.DataFrame | None = None,
    llm_gate: LLMSignalGate | None = None,
    top_n: int = 2,
    risk: RiskLimits | None = None,
) -> IntradayFlatStrategy:
    """One-stop test factory. Caches per-symbol DFs in context.dfs so
    later steps can re-use the same synthetic histories."""
    if map_mapping is None:
        # Every candidate gets a stub epic by default.
        map_mapping = {sym: f"TEST.D.{sym}.CASH.IP" for sym in candidates}
    if data_overrides is None:
        data_overrides = {}

    # Default uptrending histories for any candidate not overridden.
    # Drifts chosen so the strongest is IWM, and seeds are hand-pinned
    # to keep tests reproducible regardless of PYTHONHASHSEED. Noise
    # is kept small enough that the drift dominates the recent bars
    # (the Ichimoku cloud is computed from displaced senkou values, so
    # a recent dip can knock a symbol out of "above cloud" status even
    # with a strong cumulative drift).
    dfs: dict[str, pd.DataFrame] = {}
    drifts = {"SPY": 0.30, "QQQ": 0.25, "IWM": 0.50, "DIA": 0.20,
              "XLF": 0.15, "AAPL": 0.35}
    seeds = {"SPY": 101, "QQQ": 202, "IWM": 303, "DIA": 404,
             "XLF": 505, "AAPL": 606}
    for sym in candidates:
        if sym in data_overrides:
            dfs[sym] = data_overrides[sym]
        else:
            dfs[sym] = _make_daily_df(
                start_price=200.0 + drifts.get(sym, 0.2) * 100,
                drift=drifts.get(sym, 0.2),
                noise=0.25,                       # tight enough trend
                direction="up",
                seed=seeds.get(sym, 999),
            )
    # Regime symbol — default uptrend if not specified.
    regime_sym = "SPY"
    if use_regime_filter:
        if regime_symbol_df is not None:
            dfs[regime_sym] = regime_symbol_df
        elif regime_sym not in dfs:
            dfs[regime_sym] = _make_daily_df(start_price=400.0, drift=0.3)

    epic_map_path = _write_epic_map(map_mapping)

    strat = IntradayFlatStrategy(
        strategy_id="it_test",
        params={
            "candidates": candidates,
            "top_n": top_n,
            "use_regime_filter": use_regime_filter,
            "regime_symbol": regime_sym,
            "ig_epic_map_path": str(epic_map_path),
            # Wide entry window so in-window bars at 14:30 UTC pass;
            # pre-window step uses an earlier timestamp explicitly.
            "entry_window_start_utc": "13:35",
            "entry_window_end_utc": "19:00",
            "flatten_start_utc": "19:50",
            "session_close_utc": "20:00",
            "_data_fn": lambda s: dfs.get(s),
            "_llm_gate": llm_gate,
        },
        risk=risk or RiskLimits(
            max_open_positions=top_n, allow_short=False,
        ),
    )
    context.strat = strat
    context.dfs = dfs
    context.epic_map_path = epic_map_path
    return strat


def _make_bar(
    symbol: str,
    *,
    ts: datetime,
    close: float = 100.0,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=high if high is not None else close * 1.005,
        low=low if low is not None else close * 0.995,
        close=close,
        volume=100_000,
        timeframe_seconds=60,
    )


# ====================================================================== #
# Section 1: Scanner                                                       #
# ====================================================================== #


@given('an IntradayFlatStrategy with candidates "{csv}" mapped to IG epics')
def step_strategy_with_candidates(context, csv: str) -> None:
    candidates = [s.strip() for s in csv.split(",")]
    _build_strategy(context, candidates)


@given('an IntradayFlatStrategy with candidates "{csv}" mapped to IG epics only for "{mapped}"')
def step_strategy_partial_mapping(context, csv: str, mapped: str) -> None:
    candidates = [s.strip() for s in csv.split(",")]
    mapping: dict[str, str | None] = {}
    for sym in candidates:
        mapping[sym] = f"TEST.D.{sym}.CASH.IP" if sym == mapped else None
    _build_strategy(context, candidates, map_mapping=mapping)


@given('synthetic uptrending daily history with the strongest trend on "{sym}"')
def step_strongest_on(context, sym: str) -> None:
    # default factories already use drifts that put IWM strongest.
    assert sym in context.dfs, f"{sym!r} not in candidates"


@given('synthetic uptrending daily history (IWM strongest drift, QQQ weakest)')
def step_drifts_clear(context) -> None:
    # Drift assignments in the factory are: IWM=0.50, SPY=0.30, QQQ=0.25.
    # Strength score depends on price-vs-kijun and cloud thickness, not
    # raw drift, so ordering of IWM vs SPY is not guaranteed — but QQQ
    # being weakest means it's the one dropped at top-2.
    return None


@then('the locked basket contains "{sym}"')
def step_basket_contains(context, sym: str) -> None:
    assert sym in context.strat._basket, (
        f"{sym!r} not in basket {context.strat._basket!r}"
    )


@given('synthetic uptrending daily history')
def step_uptrending_default(context) -> None:
    return None  # default factory already uptrends


@given('synthetic uptrending daily history for the candidates')
def step_uptrending_candidates(context) -> None:
    return None


@given('synthetic uptrending daily history for "{up_sym}" and flat history for "{flat_sym}"')
def step_mixed_trend(context, up_sym: str, flat_sym: str) -> None:
    context.dfs[flat_sym] = _make_daily_df(
        start_price=100.0, drift=0.0, noise=0.05, direction="flat", seed=7,
    )


@given('synthetic DOWNTRENDING history for the regime symbol "{sym}"')
def step_regime_downtrend(context, sym: str) -> None:
    df = _make_daily_df(start_price=500.0, drift=0.6, direction="down", seed=11)
    context.dfs[sym] = df
    # Rebuild the strategy with regime ON + this DF (steps run sequentially;
    # the previous step constructed the strategy already, so we rewire its
    # _data_fn to return the new SPY DF).
    context.strat.params["_data_fn"] = lambda s: context.dfs.get(s)
    context.strat.params["use_regime_filter"] = True


@given('the regime filter disabled')
def step_regime_off(context) -> None:
    context.strat.params["use_regime_filter"] = False


@given('the regime filter enabled')
def step_regime_on(context) -> None:
    context.strat.params["use_regime_filter"] = True


@when('I call on_session_start')
def step_session_start(context) -> None:
    context.strat.on_session_start(_SESSION_DATE)


@then('the locked basket starts with "{sym}"')
def step_basket_first(context, sym: str) -> None:
    assert context.strat._basket, "basket is empty"
    actual = context.strat._basket[0]
    assert actual == sym, f"basket[0]={actual!r} expected {sym!r}; full basket={context.strat._basket!r}"


@then('the basket has exactly {n:d} symbols')
def step_basket_count(context, n: int) -> None:
    actual = len(context.strat._basket)
    assert actual == n, f"basket size {actual} != {n}; basket={context.strat._basket!r}"


@then('the basket is empty')
def step_basket_empty(context) -> None:
    assert context.strat._basket == [], f"basket not empty: {context.strat._basket!r}"


@then('a "{action}" decision is logged for "{symbol}"')
@then('an "{action}" decision is logged for "{symbol}"')
def step_decision_logged(context, action: str, symbol: str) -> None:
    buf = context.strat._decisions.get(symbol)
    assert buf, f"no decisions logged for {symbol!r}"
    actions = [d["action"] for d in buf]
    assert action in actions, (
        f"action {action!r} not in decisions for {symbol!r}: {actions}"
    )


@then('a "{action}" decision is logged for the rejected candidate')
def step_decision_rejected(context, action: str) -> None:
    # The rejected candidate is the one NOT in the basket among the candidates
    candidates = context.strat._p()["candidates"]
    rejected = [s for s in candidates if s not in context.strat._basket]
    assert rejected, "no rejected candidate"
    for sym in rejected:
        buf = context.strat._decisions.get(sym)
        if buf and any(d["action"] == action for d in buf):
            return
    raise AssertionError(
        f"no rejected candidate had a {action!r} decision; "
        f"rejected={rejected}"
    )


@then('a "{action}" decision is logged for both symbols')
def step_decision_logged_both(context, action: str) -> None:
    syms = context.both_symbols
    for sym in syms:
        buf = context.strat._decisions.get(sym)
        assert buf, f"no decisions for {sym!r}"
        actions = [d["action"] for d in buf]
        assert action in actions, f"{action} not in {sym!r}: {actions}"


@then('"{sym}" is not in the basket')
def step_not_in_basket(context, sym: str) -> None:
    assert sym not in context.strat._basket, (
        f"{sym!r} unexpectedly in basket {context.strat._basket!r}"
    )


# ====================================================================== #
# Section 2: Entry pipeline                                                #
# ====================================================================== #


@given('an IntradayFlatStrategy with locked basket "{csv}"')
def step_strategy_with_locked_basket(context, csv: str) -> None:
    basket = [s.strip() for s in csv.split(",")]
    _build_strategy(context, basket, top_n=len(basket))
    # Run the scanner to lock the basket from real synthetic data.
    context.strat.on_session_start(_SESSION_DATE)
    # If the synthetic data didn't produce exactly this basket
    # (because of strength ordering), forcibly seed it for the test.
    if set(context.strat._basket) != set(basket):
        context.strat._basket = basket
        for sym in basket:
            context.strat._basket_atr.setdefault(sym, 1.0)
            context.strat._basket_strength.setdefault(sym, 1.0)
            context.strat._basket_meta.setdefault(sym, {})


@given('an IntradayFlatStrategy with locked basket "{csv}" and a VETOING LLM gate')
def step_strategy_basket_veto_llm(context, csv: str) -> None:
    basket = [s.strip() for s in csv.split(",")]
    gate = _stub_llm_gate(verdict="VETOED")
    _build_strategy(context, basket, top_n=len(basket), llm_gate=gate)
    context.strat.on_session_start(_SESSION_DATE)
    if set(context.strat._basket) != set(basket):
        context.strat._basket = basket
        for sym in basket:
            context.strat._basket_atr.setdefault(sym, 1.0)
            context.strat._basket_strength.setdefault(sym, 1.0)


@given('an IntradayFlatStrategy with locked basket "{csv}" and a BOOSTING LLM gate')
def step_strategy_basket_boost_llm(context, csv: str) -> None:
    basket = [s.strip() for s in csv.split(",")]
    gate = _stub_llm_gate(verdict="BOOSTED", scale=2.0)
    _build_strategy(context, basket, top_n=len(basket), llm_gate=gate)
    context.strat.on_session_start(_SESSION_DATE)
    if set(context.strat._basket) != set(basket):
        context.strat._basket = basket
        for sym in basket:
            context.strat._basket_atr.setdefault(sym, 1.0)
            context.strat._basket_strength.setdefault(sym, 1.0)
    context.boosted = True


@given('the strategy has already emitted an entry for "{sym}" this session')
def step_already_entered(context, sym: str) -> None:
    context.strat._entries_today.add(sym)


@given('the risk envelope is halted with reason "{reason}"')
def step_halt_risk(context, reason: str) -> None:
    assert context.strat.risk is not None
    context.strat.risk.halted = True
    context.strat.risk.halt_reason = reason


@when('I feed one in-window bar for "{sym}"')
def step_feed_in_window(context, sym: str) -> None:
    bar = _make_bar(sym, ts=_IN_WINDOW_TS, close=200.0)
    context.orders = context.strat.on_bar(bar)


@when('I feed one in-window bar for "{sym}" with low {low:f} and high {high:f}')
def step_feed_in_window_levels(context, sym: str, low: float, high: float) -> None:
    bar = _make_bar(sym, ts=_IN_WINDOW_TS, close=(low + high) / 2,
                    high=high, low=low)
    context.orders = context.strat.on_bar(bar)


@when('I feed one PRE-WINDOW bar for "{sym}"')
def step_feed_pre_window(context, sym: str) -> None:
    bar = _make_bar(sym, ts=_PRE_WINDOW_TS, close=200.0)
    context.orders = context.strat.on_bar(bar)


# NOTE: "no orders are emitted" is defined in paper_quant_strategies_steps.py;
# we reuse it. It asserts `context.orders == []`, which matches what our
# `step_feed_in_window` etc. assign.


@then('no decision is logged for "{sym}"')
def step_no_decision(context, sym: str) -> None:
    buf = context.strat._decisions.get(sym, [])
    assert len(buf) == 0, f"unexpected decisions for {sym!r}: {list(buf)}"


# ====================================================================== #
# Section 3: LLM                                                           #
# ====================================================================== #


def _stub_llm_gate(*, verdict: str = "APPROVED", scale: float = 1.0,
                   sentiment: float = 0.0) -> LLMSignalGate:
    """A gate that returns a canned GateDecision regardless of input.
    We bypass news+score fns by overriding `evaluate` directly."""
    gate = LLMSignalGate(LLMGateConfig(enabled=True))
    action_map = {
        "APPROVED": GateDecision.APPROVED,
        "VETOED": GateDecision.VETOED,
        "BOOSTED": GateDecision.APPROVED_BOOSTED,
    }
    action = action_map.get(verdict, GateDecision.APPROVED)

    def _fake_evaluate(symbol: str, signal_strength: float) -> GateDecision:
        return GateDecision(
            action=action,
            scale_factor=scale,
            reason=f"stub verdict={verdict}",
            sentiment_score=sentiment,
            headlines_checked=1,
            provider_used="stub",
        )
    gate.evaluate = _fake_evaluate  # type: ignore[assignment]
    return gate


# NOTE: 'a BUY MARKET order is emitted for "{sym}"' is defined in
# paper_quant_strategies_steps.py. It checks len(context.orders) == 1
# and the symbol/side/type. Our scenarios that emit exactly one BUY
# market order reuse it; we add the no-arg "context.last_order =
# context.orders[0]" via the @step hook below so downstream steps
# (e.g. "the emitted quantity is greater than baseline") can find it.


@then('the emitted quantity is greater than the unboosted baseline')
def step_qty_boosted(context) -> None:
    # Recompute baseline by running the same strategy with a no-op gate.
    # Use the same params + fresh state so the math is comparable.
    boosted_order = context.orders[0]
    p = dict(context.strat.params)
    p["_llm_gate"] = None  # no gate = no boost
    baseline_strat = IntradayFlatStrategy(
        strategy_id=context.strat.strategy_id + "_baseline",
        params=p,
        risk=context.strat.risk,
    )
    baseline_strat._basket = list(context.strat._basket)
    baseline_strat._basket_atr = dict(context.strat._basket_atr)
    baseline_strat._basket_strength = dict(context.strat._basket_strength)
    baseline_strat._regime_bull = True
    bar = _make_bar(boosted_order.symbol, ts=_IN_WINDOW_TS, close=200.0)
    baseline_orders = baseline_strat.on_bar(bar)
    assert baseline_orders, "baseline strategy emitted nothing — comparison invalid"
    assert boosted_order.quantity > baseline_orders[0].quantity, (
        f"boosted qty {boosted_order.quantity} <= "
        f"baseline {baseline_orders[0].quantity}"
    )


# ====================================================================== #
# Section 4: Position management                                          #
# ====================================================================== #


def _seed_open_position(
    context, sym: str, *, qty: int = 10, entry: float = 200.0,
    stop: float = 198.0, target: float = 210.0,
    opened_at: datetime | None = None,
) -> None:
    """Manually push the strategy into a held-position state."""
    pos = context.strat.position_for(sym)
    pos.quantity = qty
    pos.avg_entry_price = entry
    context.strat._position_stop[sym] = stop
    context.strat._position_target[sym] = target
    context.strat._position_entry_price[sym] = entry
    context.strat._position_open_at[sym] = opened_at or _IN_WINDOW_TS
    context.strat._basket_atr.setdefault(sym, 1.0)
    context.strat._basket_strength.setdefault(sym, 1.0)


@given('an IntradayFlatStrategy holding an "{sym}" long with stop {stop:f} and target {target:f}')
def step_holding_position(context, sym: str, stop: float, target: float) -> None:
    _build_strategy(context, [sym], top_n=1)
    context.strat._basket = [sym]
    _seed_open_position(context, sym, stop=stop, target=target)


@given('an IntradayFlatStrategy holding an "{sym}" long opened {minutes:d} minutes ago')
def step_holding_old_position(context, sym: str, minutes: int) -> None:
    _build_strategy(context, [sym], top_n=1)
    context.strat._basket = [sym]
    opened = _IN_WINDOW_TS - timedelta(minutes=minutes)
    _seed_open_position(context, sym, opened_at=opened)


@given('an IntradayFlatStrategy holding open longs in "{a}" and "{b}"')
def step_holding_two(context, a: str, b: str) -> None:
    _build_strategy(context, [a, b], top_n=2)
    context.strat._basket = [a, b]
    _seed_open_position(context, a)
    _seed_open_position(context, b)
    context.both_symbols = [a, b]


@given('an IntradayFlatStrategy holding an "{sym}" long and a VETOING LLM gate')
def step_holding_with_veto(context, sym: str) -> None:
    gate = _stub_llm_gate(verdict="VETOED")
    _build_strategy(context, [sym], top_n=1, llm_gate=gate)
    context.strat._basket = [sym]
    _seed_open_position(context, sym)


@given('an IntradayFlatStrategy holding an "{sym}" long')
def step_holding_simple(context, sym: str) -> None:
    _build_strategy(context, [sym], top_n=1)
    context.strat._basket = [sym]
    _seed_open_position(context, sym)


# NOTE: 'a SELL MARKET order is emitted for "{sym}"' is in
# paper_quant_strategies_steps.py too. Same reuse pattern.


@then('SELL MARKET orders are emitted for both "{a}" and "{b}"')
def step_sell_market_for_both(context, a: str, b: str) -> None:
    syms = {o.symbol for o in context.orders or []
            if o.side == OrderSide.SELL and o.type == OrderType.MARKET}
    assert syms == {a, b}, f"expected sells for {{{a}, {b}}}, got {syms}"
    context.both_symbols = [a, b]


# NOTE: 'the order tag contains "{needle}"' is in paper_quant_strategies_steps.py
# and asserts on context.orders[0].tag — works for our single-order cases.


@then('both order tags contain "{token}"')
def step_both_tags_contain(context, token: str) -> None:
    for o in context.orders or []:
        assert token in (o.tag or ""), f"order tag missing {token!r}: {o.tag!r}"


# ====================================================================== #
# Section 5: EOD                                                           #
# ====================================================================== #


@when('I feed one EOD-WINDOW bar')
def step_feed_eod_anonymous(context) -> None:
    # Use the first held symbol — _build_eod_flatten_orders iterates ALL
    # positions, so the bar's symbol matters only for the timestamp.
    sym = context.both_symbols[0] if hasattr(context, "both_symbols") else next(iter(context.strat.positions))
    bar = _make_bar(sym, ts=_EOD_WINDOW_TS, close=200.0)
    context.orders = context.strat.on_bar(bar)


@when('I feed one EOD-WINDOW bar for "{sym}"')
def step_feed_eod_window(context, sym: str) -> None:
    bar = _make_bar(sym, ts=_EOD_WINDOW_TS, close=200.0)
    context.orders = context.strat.on_bar(bar)


@when('I call on_session_end without flattening first')
def step_session_end_with_open(context) -> None:
    context.strat.on_session_end(_SESSION_DATE)


# ====================================================================== #
# Section 6: Overnight leftovers                                          #
# ====================================================================== #


def _parse_qty_dict(text: str) -> dict[str, int]:
    """Parse "{AAPL: 22, MSFT: 0, GOOG: -5}" into {AAPL: 22, ...}."""
    inner = text.strip().lstrip("{").rstrip("}")
    out: dict[str, int] = {}
    for part in inner.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip()] = int(v.strip())
    return out


@when('I call seed_positions with "{payload}"')
def step_seed_positions(context, payload: str) -> None:
    context.strat.seed_positions(_parse_qty_dict(payload))


@given('initial_positions "{payload}" passed via params')
def step_initial_positions_param(context, payload: str) -> None:
    context.strat.params["initial_positions"] = _parse_qty_dict(payload)


@then('the strategy\'s position for "{sym}" is {qty:d} shares')
def step_position_qty(context, sym: str, qty: int) -> None:
    actual = context.strat.position_for(sym).quantity
    assert actual == qty, f"position[{sym}] = {actual} != {qty}"


@given(
    'an IntradayFlatStrategy with a {qty:d}-share overnight leftover '
    'in "{sym}"'
)
def step_with_leftover(context, qty: int, sym: str) -> None:
    _build_strategy(context, ["IWM"], top_n=1)
    context.strat.on_session_start(_SESSION_DATE)
    context.strat.seed_positions({sym: qty})


@given(
    'an IntradayFlatStrategy basket "{csv}" plus a {qty:d}-share '
    'overnight leftover in "{leftover_sym}"'
)
def step_basket_with_leftover(
    context, csv: str, qty: int, leftover_sym: str,
) -> None:
    basket = [s.strip() for s in csv.split(",")]
    _build_strategy(context, basket, top_n=len(basket))
    context.strat.on_session_start(_SESSION_DATE)
    if set(context.strat._basket) != set(basket):
        context.strat._basket = basket
        for sym in basket:
            context.strat._basket_atr.setdefault(sym, 1.0)
            context.strat._basket_strength.setdefault(sym, 1.0)
    context.strat.seed_positions({leftover_sym: qty})


# ====================================================================== #
# Section 7: Concurrency + halt                                           #
# ====================================================================== #


@given('the strategy has an entry order in-flight for "{sym}"')
def step_in_flight(context, sym: str) -> None:
    context.strat.mark_order_in_flight(sym)


@given(
    'an IntradayFlatStrategy with locked basket "{csv}" and top_n {n:d}'
)
def step_basket_topn(context, csv: str, n: int) -> None:
    basket = [s.strip() for s in csv.split(",")]
    _build_strategy(context, basket, top_n=n)
    context.strat.on_session_start(_SESSION_DATE)
    # Force the basket to exactly match the spec for deterministic tests
    context.strat._basket = basket
    for sym in basket:
        context.strat._basket_atr.setdefault(sym, 1.0)
        context.strat._basket_strength.setdefault(sym, 1.0)


@given('the strategy already holds {n:d} open position in "{sym}"')
@given('the strategy already holds {n:d} open positions in "{sym}"')
def step_already_holds(context, n: int, sym: str) -> None:
    pos = context.strat.position_for(sym)
    pos.quantity = n if n > 0 else -abs(n)
    pos.avg_entry_price = 200.0
    context.strat._position_stop[sym] = 195.0
    context.strat._position_target[sym] = 210.0
    context.strat._position_entry_price[sym] = 200.0
    context.strat._position_open_at[sym] = _IN_WINDOW_TS


# ====================================================================== #
# Section 8: on_fill + LLM fail-open                                      #
# ====================================================================== #


@given('an IntradayFlatStrategy with locked basket "{csv}" and ATR {atr:f}')
def step_basket_with_atr(context, csv: str, atr: float) -> None:
    basket = [s.strip() for s in csv.split(",")]
    _build_strategy(context, basket, top_n=len(basket))
    context.strat.on_session_start(_SESSION_DATE)
    context.strat._basket = basket
    for sym in basket:
        context.strat._basket_atr[sym] = atr
        context.strat._basket_strength.setdefault(sym, 1.0)


@when(
    'I emit an entry for "{sym}" and the fill price differs from the bar close'
)
def step_emit_and_fill(context, sym: str) -> None:
    # Emit an entry by feeding an in-window bar; capture the order; then
    # synthesize a fill at a price OFFSET from the bar close.
    bar_close = 200.0
    bar = _make_bar(sym, ts=_IN_WINDOW_TS, close=bar_close)
    orders = context.strat.on_bar(bar)
    assert orders, "expected at least one order"
    order = orders[0]
    context.entry_order = order
    fill_price = bar_close + 1.25  # divergent from bar.close
    context.fill_price = fill_price
    # Simulate the engine: apply the fill to the position first, then call on_fill.
    pos = context.strat.position_for(sym)
    pos.quantity = order.quantity
    pos.avg_entry_price = fill_price
    fill = Fill(
        order_id="o1",
        strategy_id=context.strat.strategy_id,
        symbol=sym,
        side=OrderSide.BUY,
        quantity=order.quantity,
        fill_price=fill_price,
        fill_time=_IN_WINDOW_TS,
        commission=0.0,
    )
    context.strat.on_fill(fill)
    context.fill_symbol = sym


@then('the position\'s stop is anchored to fill_price minus stop_atr_mult times ATR')
def step_stop_anchored(context) -> None:
    sym = context.fill_symbol
    expected = context.fill_price - (
        float(context.strat._p()["stop_atr_mult"])
        * context.strat._basket_atr[sym]
    )
    actual = context.strat._position_stop[sym]
    assert abs(actual - expected) < 1e-6, (
        f"stop {actual} != expected {expected}"
    )


@then('the position\'s target is anchored to fill_price plus target_atr_mult times ATR')
def step_target_anchored(context) -> None:
    sym = context.fill_symbol
    expected = context.fill_price + (
        float(context.strat._p()["target_atr_mult"])
        * context.strat._basket_atr[sym]
    )
    actual = context.strat._position_target[sym]
    assert abs(actual - expected) < 1e-6, (
        f"target {actual} != expected {expected}"
    )


# ====================================================================== #
# Section 9: Daemon wiring                                                #
# ====================================================================== #


@when('I import tradepro_strategies.paper.strategies')
def step_import_strategies(context) -> None:
    # The import-side-effect IS the test: importing the package runs
    # `from .intraday_flat import IntradayFlatStrategy` which fires the
    # @register_strategy decorator. If the import is missing, the
    # registry lookup below fails.
    import tradepro_strategies.paper.strategies as _strategies_pkg
    context.strategies_pkg = _strategies_pkg


@when("I import the intraday engine's default strategy list")
def step_import_intraday_defaults(context) -> None:
    from tradepro_strategies.cli.intraday_engine import (
        _INTRADAY_DEFAULT_STRATEGY_NAMES,
    )
    context.intraday_default_names = list(_INTRADAY_DEFAULT_STRATEGY_NAMES)


@then('the strategy "{name}" is registered in the global registry')
def step_strategy_registered(context, name: str) -> None:
    from tradepro_strategies.paper.registry import list_names
    available = list_names()
    assert name in available, (
        f"{name!r} not in registry; available={sorted(available)}"
    )


@then('the strategy "{name}" can be built by name')
def step_strategy_buildable(context, name: str) -> None:
    from tradepro_strategies.paper.strategies import build
    inst = build(name, strategy_id=f"{name}_test")
    assert inst is not None
    assert inst.strategy_id == f"{name}_test"


@then('every name in the default list is in the registry')
def step_defaults_all_registered(context) -> None:
    from tradepro_strategies.paper.registry import list_names
    available = set(list_names())
    missing = [n for n in context.intraday_default_names if n not in available]
    assert not missing, (
        f"these default-list strategies aren't in the registry "
        f"(import gap in paper/strategies/__init__.py?): {missing}"
    )


@given(
    'an IntradayFlatStrategy with locked basket "{csv}" '
    'and an ERRORING LLM gate'
)
def step_basket_erroring_llm(context, csv: str) -> None:
    basket = [s.strip() for s in csv.split(",")]
    gate = LLMSignalGate(LLMGateConfig(enabled=True))

    def _raises(symbol: str, signal_strength: float) -> GateDecision:
        raise RuntimeError("synthetic LLM provider crash")

    gate.evaluate = _raises  # type: ignore[assignment]
    _build_strategy(context, basket, top_n=len(basket), llm_gate=gate)
    context.strat.on_session_start(_SESSION_DATE)
    if set(context.strat._basket) != set(basket):
        context.strat._basket = basket
        for sym in basket:
            context.strat._basket_atr.setdefault(sym, 1.0)
            context.strat._basket_strength.setdefault(sym, 1.0)
