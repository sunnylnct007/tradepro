"""The shown arithmetic must actually be the arithmetic.

The momentum drill-down prints a `formula` for every number — "entry x 0.92 =
160.04" — so a reader can CHECK it rather than trust it. That only helps if
the string and the value cannot drift apart, which is what this pins.

Built on a synthetic series rather than live bars because the screen is
frequently empty (the rule takes only the first touch of the 10-SMA), and a
test that silently passes on an empty list tests nothing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradepro_strategies.cli import momentum_candidates as m


def _uptrend_with_pullback():
    """220 rising sessions, then a dip that lands on the 10-day average.

    Shaped to satisfy every clause: above the 200-SMA, 20-SMA above the
    50-SMA, close above the 20-SMA, close back at the 10-SMA, and above it
    the session before.
    """
    closes = [100.0 + i * 0.5 for i in range(300)]     # steady uptrend
    closes[-1] = closes[-2] - 3.0                       # final bar dips to the 10-SMA
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame(
        {"open": closes, "high": [c * 1.004 for c in closes],
         "low": [c * 0.996 for c in closes], "close": closes,
         "volume": [1_000_000] * len(closes)},
        index=idx)


@pytest.fixture
def row(monkeypatch):
    df = _uptrend_with_pullback()
    monkeypatch.setattr(m, "_load", lambda sym: df)
    monkeypatch.setattr(m, "poison_check", lambda c, v=None: (True, 0))
    monkeypatch.setattr(m, "_pick_signal_index", lambda dates, last: len(dates) - 1)
    rows, _ = m.scan(["TEST"])
    assert rows, "the synthetic series should fire the entry — if not, the fixture is wrong"
    return rows[0]


def test_stop_is_eight_percent_below_entry(row):
    assert row["stop"] == pytest.approx(row["entry_hint"] * (1 - m.STOP_PCT), abs=0.01)


def test_stop_formula_states_the_numbers_it_used(row):
    f = row["calcs"]["stop"]["formula"]
    assert f"{row['entry_hint']:.2f}" in f
    assert f"{row['stop']:.2f}" in f


def test_risk_per_share_equals_entry_minus_stop(row):
    rps = row["calcs"]["risk_per_share"]["value"]
    assert rps == pytest.approx(row["entry_hint"] - row["stop"], abs=0.02)


def test_pullback_depth_matches_the_trigger_condition(row):
    """The rule fires when close <= 10-SMA x 1.005, so the reported depth must
    be at or below +0.5%. A positive number here would mean the screen is
    describing a breakout while claiming a pullback."""
    assert row["calcs"]["pullback_depth"]["value"] <= 0.5


def test_vs_200sma_is_positive_because_the_rule_requires_it(row):
    assert row["calcs"]["vs_200sma"]["value"] > 0


def test_every_calc_carries_a_why_and_a_formula(row):
    for name, c in row["calcs"].items():
        assert c.get("why"), f"{name} has no explanation"
        assert c.get("formula"), f"{name} has no formula"
        assert "=" in c["formula"], f"{name}'s formula shows no arithmetic: {c['formula']}"


def test_trailing_stop_is_quoted_off_the_peak_not_the_entry(row):
    """A trailing stop quoted off the entry would be a second fixed stop. The
    wording has to say peak, because that is what the backtest did."""
    assert "peak" in row["calcs"]["trailing_stop"]["formula"].lower()
