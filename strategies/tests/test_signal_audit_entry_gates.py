"""A name the entry gates REFUSE is the strategy working, not a missed buy.

4 Sep 2026: the desk showed "Missed BUYs — signal says LONG, we're flat (47)"
against 8 held. The audit asked only `position == 1.0` and ignored every gate
the live strategy applies — entry_rsi_max=80, entry_max_ext_pct=50,
entry_max_kijun_atr=1.5, all present in runtime_config and all unused here.

So a blow-off top the strategy correctly refuses was reported as an opportunity
we fumbled. 47 undifferentiated red rows is a count nobody can act on, which is
precisely how a panel trains a reader to stop looking at it — the same
cry-wolf shape the shared-context cleanup was written to end.

The formulas are copied from ichimoku_equity's own meta block so the audit and
the strategy cannot disagree about what "too extended" means.
"""
import numpy as np
import pandas as pd
import pytest

from tradepro_strategies.cli.signal_audit import _entry_gate_verdict

GATES = {"entry_rsi_max": 80, "entry_max_ext_pct": 50, "entry_max_kijun_atr": 1.5}


def _frame(closes):
    c = pd.Series(np.asarray(closes, float))
    return pd.DataFrame({"Close": c, "High": c * 1.005, "Low": c * 0.995})


def _drift(n, start, end, wobble=0.9, seed=7):
    """An uptrend with DOWN DAYS in it — i.e. an actual price series.

    A monotonic np.linspace has zero down-days, so RSI is exactly 100 and the
    cap fires. That is the gate working correctly on an impossible input; the
    first version of this test used one and failed for that reason.
    """
    rng = np.random.default_rng(seed)
    return np.linspace(start, end, n) + rng.normal(0, wobble, n)


def test_a_calm_uptrend_would_be_bought():
    # Gentle drift with real noise: not extended, RSI mid, near the kijun.
    would, why = _entry_gate_verdict(_frame(_drift(300, 100, 118)), GATES)
    assert would is True, f"refused a calm uptrend: {why}"
    assert why == ""


def test_a_blowoff_top_is_refused_on_extension():
    # Flat for a year, then triples — far above the 200-SMA.
    base = list(np.full(260, 100.0)) + list(np.linspace(100, 320, 40))
    would, why = _entry_gate_verdict(_frame(base), GATES)
    assert would is False
    assert "200-SMA" in why or "RSI" in why or "kijun" in why


def test_the_reason_is_always_stated_when_refused():
    base = list(np.full(260, 100.0)) + list(np.linspace(100, 400, 40))
    would, why = _entry_gate_verdict(_frame(base), GATES)
    assert would is False and why, "a refusal with no reason is the old panel again"


def test_no_gates_configured_means_everything_is_a_genuine_miss():
    # With gates absent the audit must NOT invent them — it reports what the
    # strategy would do, and a strategy with no caps buys anything long.
    would, why = _entry_gate_verdict(_frame(_drift(300, 100, 400)), {})
    assert would is True and why == ""


def test_an_unevaluable_gate_does_not_silently_pass_as_refused():
    # Too little history to compute anything. It must fall through as a miss
    # WITH a note, never be quietly counted as gate-blocked — that would hide
    # real misses behind an error.
    would, why = _entry_gate_verdict(_frame([100, 101, 102]), GATES)
    assert would is True


@pytest.mark.parametrize("cap,expect_block", [(50, True), (10_000, False)])
def test_the_extension_cap_is_actually_applied(cap, expect_block):
    base = list(np.full(260, 100.0)) + list(np.linspace(100, 300, 40))
    would, _ = _entry_gate_verdict(_frame(base), {"entry_max_ext_pct": cap})
    assert (would is False) == expect_block
