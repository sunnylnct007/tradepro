"""A per-symbol P&L number is the easiest confident lie on this desk.

Owner: *"what can we do to evaluate the simulation of p/l for the selected
symbol if we decide to trade"*.

Most names have fewer than thirty instances of their own signal. That is
anecdote, not a distribution. Measured on the live store:

    AAPL  36 instances  ->  estimate given
    AES   26 instances  ->  estimate WITHHELD
    ABNB   7 instances  ->  85.7% win rate, estimate WITHHELD

ABNB's 85.7% is the number that would get acted on and must not be offered as
one. The rule here is the same one the wheel's expectancy column follows: a
sample that cannot support a figure gets a refusal, not a figure.

This pins the refusal in both directions — a thin sample must not produce an
estimate, and a genuine sample must not be silently suppressed.
"""
from __future__ import annotations

from tradepro_strategies.symbol_outlook import (
    MIN_FOR_ESTIMATE, MIN_TO_REPORT, Outlook, _summarise)


def _mk(n: int, rets=None) -> Outlook:
    rets = rets or [1.0 + 0.1 * i for i in range(n)]
    return _summarise("TEST", "mean_reversion_swing", rets,
                      [10] * n, [f"2020-01-{(i % 28) + 1:02d}" for i in range(n)],
                      "test")


def test_a_thin_sample_gets_NO_central_estimate():
    o = _mk(MIN_FOR_ESTIMATE - 1)
    assert o.estimate_is_supported is False
    assert o.mean_pct is None, (
        "a mean was published on a sample too thin to support it — this is the "
        "exact number a reader would act on")
    assert str(MIN_FOR_ESTIMATE) in " ".join(o.caveats), "the reason must be stated"


def test_the_distribution_IS_still_shown_when_thin():
    """Withholding the mean must not mean withholding everything. A range of
    20 observations still says what CAN happen; an average of 20 does not."""
    o = _mk(20)
    assert o.mean_pct is None
    for f in ("win_rate_pct", "median_pct", "worst_pct", "p10_pct", "p90_pct"):
        assert getattr(o, f) is not None, f"{f} should still be reported"


def test_a_sufficient_sample_DOES_get_an_estimate():
    """The guard must not become a hole that suppresses real evidence."""
    o = _mk(MIN_FOR_ESTIMATE)
    assert o.estimate_is_supported is True
    assert o.mean_pct is not None


def test_almost_no_history_reports_nothing_and_says_why():
    o = _mk(MIN_TO_REPORT - 1)
    assert o.instances == MIN_TO_REPORT - 1
    assert o.estimate_is_supported is False
    assert o.win_rate_pct is None
    assert "NOT a statement that the trade is bad" in o.note, (
        "absence of history must not read as a negative verdict on the trade")


def test_it_never_calls_itself_a_forecast():
    o = _mk(50)
    blob = (o.note + " " + " ".join(o.caveats)).lower()
    assert "not a forecast" in blob
    assert "does not survive a gap" in blob, (
        "the worst figure is close-checked and can be exceeded — that has to "
        "travel with it")


def test_the_rule_source_is_named_so_it_can_be_checked():
    """An outlook that cannot say WHICH rule it replayed cannot be verified
    against the rule actually being traded."""
    from tradepro_strategies.symbol_outlook import swing_outlook
    import inspect
    assert "signals.mean_reversion" in inspect.getsource(swing_outlook), (
        "the outlook must import the LIVE signal module, not restate the rule")
