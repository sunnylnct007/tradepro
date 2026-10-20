"""Grading an ENTRY, before any exit touches it.

WHY THIS EXISTS. On 20 Oct 2026 swing and momentum were measured this way for
the first time and both were found to buy moments WORSE than holding the same
names at random — after three weeks of hardening the execution path for rules
that never had an edge. The measurement took a day. It was a throwaway script
in /tmp and nobody else could have repeated it.

THE CONTROL IS THE WHOLE POINT. A raw positive number in a rising market means
nothing; both sleeves looked plausible on every internal measure and only died
when compared against the same symbols at other times.
"""
from __future__ import annotations

import pytest

from tradepro_strategies import entry_edge as E


def _flat(n=60, start=100.0, step=0.0):
    return [start + i * step for i in range(n)]


def _loader(book):
    def load(sym):
        return book.get(sym)
    return load


def test_forward_return_is_a_plain_percentage():
    assert E.forward_return([100.0, 110.0], 0, 1) == pytest.approx(10.0)


def test_forward_return_off_the_end_is_None_not_zero():
    """A signal too recent to measure must not be scored as flat."""
    assert E.forward_return([100.0, 110.0], 1, 1) is None


def test_control_A_excludes_the_signal_bar_itself():
    """Otherwise the control contains the observation it is controlling for."""
    closes = [100.0] * 10 + [200.0]        # one huge move at index 9->10
    with_bar = E.unconditional_mean(closes, 1, skip=99)   # nothing skipped
    without = E.unconditional_mean(closes, 1, skip=9)     # skip the mover
    assert with_bar > without


def test_a_rule_that_beats_both_controls_reads_as_beating_both():
    dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
    # AAA jumps right after the signal date; the benchmark does not move.
    aaa = _flat(20); aaa[11] = 120.0
    book = {"AAA": (dates, aaa), "SPY": (dates, _flat(20))}
    r = E.evaluate([("AAA", dates[10])], _loader(book), horizons=(1,))
    h = r["horizons"][1]
    assert h["beats_symbol"] is True and h["beats_benchmark"] is True
    assert h["vs_benchmark"] > 0


def test_the_swing_momentum_shape_is_detected():
    """The real finding, reproduced: the symbol drifts UP, the signal picks a
    moment that goes DOWN. A signal-only column would read -1% and look like a
    bad market; against the control it reads as a bad ENTRY."""
    dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
    closes = [100.0 + i for i in range(20)]   # steady uptrend
    closes[11] = closes[10] - 5.0             # the signal bar's next bar falls
    book = {"AAA": (dates, closes), "SPY": (dates, _flat(20))}
    r = E.evaluate([("AAA", dates[10])], _loader(book), horizons=(1,))
    h = r["horizons"][1]
    assert h["signal_pct"] < 0
    assert h["control_symbol_pct"] > 0, "control should show the underlying drift"
    assert h["beats_symbol"] is False


def test_an_unmeasurable_signal_is_NAMED_not_dropped():
    r = E.evaluate([("GONE", "2026-01-05")], _loader({}), horizons=(1,))
    assert r["unmeasured"] and r["unmeasured"][0]["symbol"] == "GONE"
    assert "no bars" in r["unmeasured"][0]["why"]


def test_a_signal_too_recent_to_measure_is_reported_as_such():
    dates = ["2026-01-01", "2026-01-02"]
    book = {"AAA": (dates, [100.0, 101.0])}
    r = E.evaluate([("AAA", "2026-01-02")], _loader(book), horizons=(5,))
    assert r["horizons"] == {}
    assert "too recent" in r["unmeasured"][0]["why"]


def test_concentration_flags_a_sign_flip():
    """One name carrying a result is the failure this desk has already had."""
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]
    up = _flat(10); up[1] = 150.0            # +50% monster
    dn = _flat(10); dn[1] = 99.0             # small loser
    book = {"BIG": (dates, up), "S1": (dates, dn), "S2": (dates, dn),
            "SPY": (dates, _flat(10))}
    r = E.evaluate([("BIG", dates[0]), ("S1", dates[0]), ("S2", dates[0])],
                   _loader(book), horizons=(1,))
    d = r["horizons"][1]["drop_top"]
    assert d["symbol"] == "BIG"
    assert d["sign_flips"] is True, "positive mean carried by one name went unflagged"


def test_first_appearance_only():
    boards = {
        "2026-01-02": {"signal_bar": "2026-01-01", "symbols": ["AAA", "BBB"]},
        "2026-01-03": {"signal_bar": "2026-01-02", "symbols": ["AAA", "CCC"]},
    }
    out = E.first_appearances(boards)
    assert out == [("AAA", "2026-01-01"), ("BBB", "2026-01-01"), ("CCC", "2026-01-02")]
    assert len([s for s, _ in out if s == "AAA"]) == 1


def test_benchmark_can_be_disabled():
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]
    book = {"AAA": (dates, _flat(10, step=1.0))}
    r = E.evaluate([("AAA", dates[0])], _loader(book), horizons=(1,), benchmark=None)
    assert r["horizons"][1]["control_benchmark_pct"] is None
    assert r["horizons"][1]["beats_benchmark"] is None
