"""One candidate record, emitted by every strategy.

Owner, 1 Sep 2026: "we want a coherant and trustworthy data and not scattered
data", then "how do i drill down to see why its a candidate".

Four producers each invented their own shape:

    swing              symbol · close · sigma_from_mean · stop
    momentum           symbol · calcs{entry,stop,atr_pct} · close
    post_earnings_puts symbol · strike_indicative · premium_usd · yield_pct
    options_screen     symbol · suggested_strike · annualized_yield_pct · blocks

So the combined Candidates view needed an adapter per strategy, in the UI — N
definitions of one thing, drifting, with nothing to fail when they disagree.
Two producers were both called "wheel" and gave 21 and 0 on the same afternoon.

The record REFUSES to be built when it cannot answer the questions a reader
needs, because a row that cannot be acted on is worse than an absent one.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.candidates import Candidate, CandidateError, emit, validate


def _ok(**kw):
    base = dict(symbol="XOM", strategy="Wheel", tier="unproven", action="sell put",
                as_of="2026-09-01T15:38:00Z")
    base.update(kw)
    return Candidate(**base)


def test_a_valid_record_round_trips():
    d = _ok(entry=160.0, level=155.0, level_label="strike",
            metric=19.1, metric_label="%/yr").to_dict()
    assert d["symbol"] == "XOM" and d["tier"] == "unproven"
    assert d["level"] == 155.0 and d["level_label"] == "strike"


def test_an_unknown_tier_is_refused():
    """'Is this strategy proven?' may not be silently absent — it is what stops
    a wheel row (backtest verdict DO NOT FUND) reading like a swing row."""
    with pytest.raises(CandidateError) as ei:
        _ok(tier="probably-fine")
    assert "tier must be one of" in str(ei.value)


def test_a_level_without_a_label_is_refused():
    """A bare number that might be a strike or a stop is worse than no number:
    one is a price you SELL at, the other a price you EXIT at."""
    with pytest.raises(CandidateError) as ei:
        _ok(level=155.0, level_label=None)
    assert "level_label" in str(ei.value)


def test_a_missing_as_of_is_refused():
    """Freshness is a PER-ROW fact — producers run on different schedules. The
    desk showed 31-Aug cards at 19:31 on 1 Sep because it was a page fact."""
    with pytest.raises(CandidateError):
        _ok(as_of="")


@pytest.mark.parametrize("kw", [{"symbol": ""}, {"strategy": ""}])
def test_an_unactionable_row_is_refused(kw):
    with pytest.raises(CandidateError):
        _ok(**kw)


def test_unknown_metric_and_entry_are_ALLOWED():
    """They are legitimately dark when a feed is. The row still says what it
    knows, and None renders as an em-dash rather than a zero — a zero yield
    would be a claim about the trade."""
    c = _ok(entry=None, metric=None)
    assert c.to_dict()["entry"] is None and c.to_dict()["metric"] is None


def test_validate_reports_every_bad_row_not_just_the_first():
    """Fixing a producer beats playing whack-a-mole with it."""
    bad = [{"symbol": "", "strategy": "Wheel", "tier": "unproven",
            "action": "x", "as_of": "t"},
           {"symbol": "A", "strategy": "Wheel", "tier": "nope",
            "action": "x", "as_of": "t"}]
    problems = validate(bad)
    assert len(problems) == 2, problems


def test_every_producer_emits_the_same_shape():
    """THE point of the phase. Each strategy's own builder must produce records
    that validate — so the UI stops needing to know their private field names."""
    from tradepro_strategies.cli.post_earnings_puts import _common_records as puts
    from tradepro_strategies.cli.swing_candidates import _common_records as swing
    from tradepro_strategies.cli.momentum_candidates import _common_records as mom
    from tradepro_strategies.cli.options_screen import _wheel_records as wheel

    at = "2026-09-01T15:38:00Z"
    rows = (
        puts([{"symbol": "MRVL", "spot": 216.6, "listed_strike": 195.0,
               "annual_yield_pct": 29.4}], at)
        + swing([{"symbol": "BC", "close": 76.6, "stop": 70.0,
                  "sigma_from_mean": -2.67}], at)
        + mom([{"symbol": "CRL", "close": 291.5, "stop": 268.2,
                "calcs": {"entry": {"value": 291.5}, "atr_pct": {"value": 2.2}}}], at)
        + wheel([{"symbol": "XOM", "ref_close": 160.0, "suggested_strike": 155.0,
                  "annualized_yield_pct": 19.1, "eligible": True}], at)
    )
    assert len(rows) == 4
    assert validate(rows) == []
    # Every strategy is represented, and each names its own tier.
    assert {r["strategy"] for r in rows} == {"Puts", "Swing", "Momentum", "Wheel"}
    tiers = {r["strategy"]: r["tier"] for r in rows}
    assert tiers["Swing"] == "gated" and tiers["Momentum"] == "gated"
    assert tiers["Wheel"] == "unproven" and tiers["Puts"] == "unproven"


def test_the_wheel_record_carries_its_gate_trace_and_provenance():
    """The drill-down's whole substance. The wheel already computes both; the
    record is what gets them to the screen."""
    from tradepro_strategies.cli.options_screen import _wheel_records
    rows = _wheel_records([{
        "symbol": "XOM", "ref_close": 160.0, "suggested_strike": 155.0,
        "annualized_yield_pct": 19.1, "eligible": True,
        "provenance": {"inputs": [{"input": "bars", "trust": "golden"}]},
        "decision_trace": [{"gate": "delta", "actual": 0.34, "verdict": "pass"}],
    }], "2026-09-01T15:38:00Z")
    assert rows[0]["provenance"] == [{"input": "bars", "trust": "golden"}]
    assert rows[0]["gates"][0]["gate"] == "delta"


def test_emit_is_the_only_serialiser():
    assert emit([_ok()]) == [_ok().to_dict()]
