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
    context.strat = strat
    context.selected = strat._selected_entries
    context.dynamic_capital = dict(strat._dynamic_capital)


@then("the selected entries are exactly STRONG, MED and WEAK")
def step_assert_all_three(context) -> None:
    assert context.selected == {"STRONG", "MED", "WEAK"}, context.selected


@then("the selected entries are exactly STRONG and MED")
def step_assert_strong_med(context) -> None:
    assert context.selected == {"STRONG", "MED"}, context.selected


@then("each is weighted 1/max(2,3) of sleeve capital")
def step_assert_equal_weight(context) -> None:
    # No top-N cut: 3 names signal into a 2-slot sleeve, so each is weighted
    # 1/max(sleeve_size=2, n_signal=3) = 1/3 of sleeve capital — equal across.
    p = context.strat._p()
    expected = float(p["capital_usd"]) / 1 / 3.0   # one sleeve in this test
    caps = [context.dynamic_capital[s] for s in ("STRONG", "MED", "WEAK")]
    for sym, cap in zip(("STRONG", "MED", "WEAK"), caps):
        assert abs(cap - expected) < 1e-6, f"{sym}: {cap} != {expected}"
    assert len({round(c, 6) for c in caps}) == 1, f"not equal-weight: {caps}"


@then("WEAK is held (signalling) but not re-entered")
def step_assert_weak_held_not_reentered(context) -> None:
    # WEAK still signals → carries a sleeve weight (in _dynamic_capital) and
    # on_bar HOLDS it, but it's not a fresh ENTRY (already at the broker).
    assert "WEAK" in context.dynamic_capital, context.dynamic_capital
    assert "WEAK" not in context.selected, context.selected
