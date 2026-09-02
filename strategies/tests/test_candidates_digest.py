"""ONE email across every strategy, each row with its tier and its freshness.

Owner, 1 Sep 2026: "not 2 diff emails", "as user i dont have to think many
screens". Phase 5 of docs/COHERENT_CANDIDATES_PLAN.md.

FOUR SENDERS mailed this account on different schedules with different
universes and no shared idea of what a candidate is. Two were both called
"wheel" and reported 21 eligible and 0 candidates on the same afternoon — and
the zero was not even a verdict, it was a screen scoring on snapshot fields it
never received.

This digest is only possible because Phase 3 gave every producer the same
record: it reads `candidates_v2` and knows NOTHING about any strategy's private
field names. A fifth strategy costs a row, not an adapter.

TWO RULES IT MAY NOT BEND

  * TIER TRAVELS WITH EVERY ROW. A candidate from a sleeve whose backtest said
    DO NOT FUND must never read like one from a sleeve that passed its gates.
  * FRESHNESS IS PER ROW. Producers run on different schedules. The desk showed
    31-Aug cards at 19:31 on 1 Sep because freshness was a page-level fact.

And "could not load" is NEVER rendered as "no candidates": silence about a
missing strategy is indistinguishable from a strategy with nothing to show.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from tradepro_strategies.cli.candidates_digest import _age_h, render

NOW = _dt.datetime(2026, 9, 1, 22, 0, tzinfo=_dt.UTC)


def _row(**kw):
    base = dict(symbol="XOM", strategy="Wheel", tier="unproven", action="sell put",
                entry=160.95, level=155.0, level_label="strike",
                metric=15.3, metric_label="%/yr", why="clears every gate", _age_h=6)
    base.update(kw)
    return base


def test_tier_is_stated_for_every_strategy_group():
    """THREE states now, not two (2 Sep 2026). "Not yet shown to work" and
    "shown not to work" are opposite claims; the owner reading a wall of one
    word said it gave them low confidence, and they were right."""
    _, text = render([_row(tier="failed"),
                      _row(symbol="MRVL", strategy="Puts", tier="thin"),
                      _row(symbol="CRL", strategy="Momentum", tier="gated")], [], NOW)
    assert "[failed]" in text and "[thin]" in text and "[gated]" in text
    assert "BACKTEST FAILED" in text
    assert "thin evidence" in text
    assert "passed its pre-registered gates" in text


def test_a_stale_row_is_marked_and_a_fresh_one_is_not():
    _, text = render([_row(symbol="OLD", _age_h=26), _row(symbol="NEW", _age_h=3)],
                     [], NOW)
    lines = {ln.split()[0]: ln for ln in text.splitlines() if ln.strip().startswith(("OLD", "NEW"))}
    assert "STALE" in lines["OLD"] and "26" in lines["OLD"]
    assert "STALE" not in lines["NEW"]


def test_a_failed_strategy_is_named_not_silently_dropped():
    """The distinction that matters: a strategy that could not load is NOT a
    strategy with no candidates."""
    _, text = render([], ["Swing: HTTP 500"], NOW)
    assert "COULD NOT LOAD" in text
    assert "not the same as 'no candidates'" in text
    assert "Swing: HTTP 500" in text


def test_no_candidates_is_framed_as_a_verdict():
    subject, text = render([], [], NOW)
    assert "none today" in subject
    assert "VERDICT, not a failure" in text
    assert "minority" in text


def test_rows_are_grouped_by_strategy():
    _, text = render([_row(symbol="A", strategy="Wheel"),
                      _row(symbol="B", strategy="Momentum", tier="gated"),
                      _row(symbol="C", strategy="Wheel")], [], NOW)
    assert text.count("── Wheel") == 1, "a strategy must appear as ONE group"
    assert text.count("── Momentum") == 1


def test_a_missing_number_renders_as_a_dash_never_a_zero():
    """A zero yield is a claim about the trade; an unknown one is not."""
    _, text = render([_row(metric=None, entry=None, level=None)], [], NOW)
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("XOM"))
    assert "—" in line and "0.0" not in line


@pytest.mark.parametrize("as_of,expected", [
    ("2026-09-01T20:00:00Z", 2.0),
    ("2026-09-01T20:00:00+00:00", 2.0),
    ("2026-09-01T20:00:00", 2.0),        # naive is treated as UTC
])
def test_age_parses_the_timestamp_shapes_producers_emit(as_of, expected):
    assert _age_h(as_of, NOW) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("bad", [None, "", "not-a-date"])
def test_an_unparseable_as_of_is_unknown_not_zero(bad):
    """Age None means "we cannot tell"; 0 would mean "fresh", which is a lie."""
    assert _age_h(bad, NOW) is None


def test_the_subject_counts_strategies_not_just_rows():
    subject, _ = render([_row(symbol="A", strategy="Wheel"),
                         _row(symbol="B", strategy="Momentum", tier="gated")], [], NOW)
    assert "2 across 2 strategies" in subject
