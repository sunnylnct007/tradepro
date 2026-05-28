"""Steps for volume_ratio.feature — pin the today / 20-day-avg volume
ratio in MarketState plus its decision_trace row.

Reuses `When I compute the market state` from range_position_steps.py."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from behave import given, then


def _series_with_volume(
    closes: list[float],
    volumes: list[float] | None,
) -> pd.DataFrame:
    """OHLCV frame on a business-day index. When `volumes` is None, no
    volume column is attached (mirrors how some Yahoo indices ship —
    ^FTSE etc. don't carry per-bar volume)."""
    # Pad the date index so freq="B" doesn't silently drop a bar at
    # year/holiday boundaries — same pattern as market_state_fields_steps.
    dates = pd.date_range(end=datetime(2026, 5, 9),
                          periods=len(closes) + 10, freq="B")
    df = pd.DataFrame(
        {"adj_close": closes, "close": closes},
        index=dates[-len(closes):],
    )
    if volumes is not None:
        df["volume"] = volumes
    return df


def _constant_walk(n: int) -> list[float]:
    """Tiny upward drift so RSI / SMA / momentum can compute. The ratio
    we care about is on volume, not price — closes are filler."""
    return [100.0 + i * 0.1 for i in range(n)]


@given("a {n:d}-bar price series with constant volume {avg:d} and today's volume {today:d}")
def step_constant_then_spike(context, n: int, avg: int, today: int) -> None:
    closes = _constant_walk(n)
    vols = [float(avg)] * (n - 1) + [float(today)]
    context.prices = _series_with_volume(closes, vols)


@given("a {n:d}-bar price series with no volume column")
def step_no_volume(context, n: int) -> None:
    closes = _constant_walk(n)
    context.prices = _series_with_volume(closes, None)


@then("volume_ratio_20d is approximately {expected:g}")
def step_ratio_close(context, expected: float) -> None:
    actual = context.state.volume_ratio_20d
    assert actual is not None, "volume_ratio_20d was None"
    assert abs(actual - expected) < 0.05, (
        f"expected ratio ~{expected}, got {actual}"
    )


@then("volume_ratio_20d is None")
def step_ratio_none(context) -> None:
    assert context.state.volume_ratio_20d is None, (
        f"expected None, got {context.state.volume_ratio_20d}"
    )


def _find_trace(state, name: str) -> dict:
    for row in state.decision_trace:
        if row.get("name") == name:
            return row
    raise AssertionError(
        f"no trace row named {name!r}; got names="
        f"{[r.get('name') for r in state.decision_trace]}"
    )


@then('the trace contains a "{name}" row with status "{status}"')
def step_trace_status(context, name: str, status: str) -> None:
    row = _find_trace(context.state, name)
    assert row["status"] == status, (
        f"trace {name!r} status is {row['status']!r}, expected {status!r}"
    )


@then('the trace detail for "{name}" mentions "{snippet}"')
def step_trace_detail(context, name: str, snippet: str) -> None:
    row = _find_trace(context.state, name)
    assert snippet in row["detail"], (
        f"trace {name!r} detail {row['detail']!r} missing {snippet!r}"
    )


@then("to_dict() carries volume_ratio_20d")
def step_dict_carries(context) -> None:
    d = context.state.to_dict()
    assert "volume_ratio_20d" in d, "to_dict() missing volume_ratio_20d"
    assert d["volume_ratio_20d"] == context.state.volume_ratio_20d
