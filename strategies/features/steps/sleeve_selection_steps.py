"""Steps for ichimoku_sleeve_selection.feature — strategy-level top-N
selection with injected synthetic OHLC (no Yahoo / network)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.paper.strategies.ichimoku_equity import (
    IchimokuEquityStrategy,
)


def _uptrend(rate: float, n: int = 120) -> pd.DataFrame:
    """An uptrend at `rate` per bar. Steeper rate ⇒ the close sits further
    above the lagging cloud ⇒ higher conviction in _compute_signal."""
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=n + 10, freq="B")[-n:]
    closes = np.array([100.0 * ((1 + rate) ** i) for i in range(n)])
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.005, "low": closes * 0.995,
         "close": closes, "adj_close": closes, "volume": [1_000_000] * n},
        index=dates,
    )


@given("three above-cloud names STRONG, MED, WEAK with decreasing conviction")
def step_three_names(context) -> None:
    context.sleeve_data = {
        "STRONG": _uptrend(0.020),
        "MED": _uptrend(0.010),
        "WEAK": _uptrend(0.004),
        "SPY": _uptrend(0.010),   # regime symbol, unused (filter off)
    }


@given('a sleeve "{name}" containing STRONG, MED, WEAK with {slots:d} slots')
def step_sleeve(context, name: str, slots: int) -> None:
    context.sleeve_name = name
    context.sleeve_slots = slots
    context.initial_positions = {}


@given("WEAK is already held from the broker seed")
def step_held(context) -> None:
    context.initial_positions = {"WEAK": 10}


@when("the equity strategy starts the session")
def step_start(context) -> None:
    data = context.sleeve_data
    strat = IchimokuEquityStrategy(
        strategy_id="eq",
        params={
            "symbols": ["STRONG", "MED", "WEAK"],
            "sleeves": {
                context.sleeve_name: {
                    "symbols": ["STRONG", "MED", "WEAK"],
                    "size": context.sleeve_slots,
                },
            },
            "use_regime_filter": False,
            "_data_fn": lambda sym: data.get(sym),
            "initial_positions": getattr(context, "initial_positions", {}),
        },
    )
    strat.on_session_start(datetime(2026, 5, 9))
    context.selected = strat._selected_entries


@then("the selected entries are exactly STRONG and MED")
def step_assert_strong_med(context) -> None:
    assert context.selected == {"STRONG", "MED"}, context.selected


@then("the selected entries are exactly STRONG")
def step_assert_strong(context) -> None:
    assert context.selected == {"STRONG"}, context.selected


@then("WEAK is dropped as below the conviction cut")
def step_assert_weak_dropped(context) -> None:
    assert "WEAK" not in context.selected, context.selected
