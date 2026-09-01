"""The digest is worth reading — charts, gates and provenance, not a text dump.

Owner, 1 Sep 2026: "but shdnt the email be roich as opposed to just a pure
text". They were right: the sender this replaced had charts, support/resistance
and written analysis, and losing those to gain coherence was paying twice for
one problem.

TWO INVARIANTS THIS PINS

  * A CHART IS A CLAIM ABOUT THE DATA. It is drawn from OUR bar store — the same
    bars the strategy screened on — or not at all. A card with too little
    history says so instead of rendering a blank frame, and never borrows bars
    from elsewhere: a chart from a different source is a different claim about
    the same day.
  * TIER IS ON EVERY CARD, never a footnote. A prettier email makes this MORE
    important, not less — presentation lends authority, and a candidate from a
    sleeve whose backtest said DO NOT FUND must not inherit it.

A BUG THIS AREA ALREADY SHIPPED: in Phase 3 the `_common_records` helper was
appended BELOW `if __name__ == "__main__":`, so running a producer as a script
raised NameError before the helper existed. Caught by running it, not by reading
it. `test_helpers_are_defined_above_the_entry_point` stops it recurring.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import pytest

from tradepro_strategies.cli.candidates_html import build_html

NOW = _dt.datetime(2026, 9, 1, 22, 0, tzinfo=_dt.UTC)
_CLI = Path(__file__).resolve().parents[1] / "tradepro_strategies" / "cli"


def _row(**kw):
    base = dict(symbol="XOM", strategy="Wheel", tier="unproven", action="sell put",
                entry=160.95, level=155.0, level_label="strike", metric=15.3,
                metric_label="%/yr", why="clears every gate", _age_h=6)
    base.update(kw)
    return base


def test_tier_appears_on_the_card_not_only_the_header():
    h = build_html([_row()], [], NOW, 20.0, with_charts=False)
    # The group header explains the tier in words ("NOT proven"); the CARD
    # carries the badge itself, so the tier survives when a reader scrolls past
    # the header — which is the whole point of putting it on the card.
    assert "NOT proven" in h, "the group header must explain the tier"
    card = h.split("NOT proven", 1)[1]
    assert "unproven" in card, "the tier badge must ride on the card too"


def test_a_stale_row_is_marked_on_its_card():
    h = build_html([_row(_age_h=26)], [], NOW, 20.0, with_charts=False)
    assert "STALE 26h" in h
    # The FOOTER always explains the rule ("rows older than 20h are marked
    # STALE"), so assert on the BADGE, not on the word appearing anywhere.
    fresh = build_html([_row(_age_h=3)], [], NOW, 20.0, with_charts=False)
    assert not re.search(r"STALE \d+h", fresh)


def test_a_missing_chart_explains_itself():
    """A blank frame is worse than a sentence — the reader cannot tell a broken
    renderer from a symbol we have no history for."""
    h = build_html([_row(symbol="ZZZZ_NOT_A_TICKER")], [], NOW, 20.0, with_charts=True)
    assert "No chart" in h
    assert "different claim about the same day" in h


def test_gates_and_provenance_render_when_published():
    h = build_html([_row(
        gates=[{"gate": "delta", "actual": 0.34, "threshold": "0.20-0.35",
                "verdict": "pass"}],
        provenance=[{"label": "Daily bars", "trust": "golden", "source_label": "IBKR"}],
    )], [], NOW, 20.0, with_charts=False)
    assert "Gates — what was checked" in h and "delta" in h
    assert "Data — where it came from" in h and "IBKR" in h


def test_they_are_omitted_rather_than_shown_empty():
    h = build_html([_row(gates=[], provenance=[])], [], NOW, 20.0, with_charts=False)
    assert "Gates — what was checked" not in h
    assert "Data — where it came from" not in h


def test_could_not_load_is_distinguished_from_no_candidates():
    h = build_html([], ["Swing: HTTP 500"], NOW, 20.0, with_charts=False)
    assert "Could not load" in h and "Swing: HTTP 500" in h
    assert "NOT the same as" in h


def test_no_candidates_is_framed_as_a_verdict():
    h = build_html([], [], NOW, 20.0, with_charts=False)
    assert "verdict, not a failure" in h


def test_symbols_are_escaped():
    h = build_html([_row(symbol="<script>x</script>")], [], NOW, 20.0, with_charts=False)
    assert "<script>" not in h and "&lt;script&gt;" in h


@pytest.mark.parametrize("mod", ["momentum_candidates.py", "swing_candidates.py",
                                 "post_earnings_puts.py"])
def test_helpers_are_defined_above_the_entry_point(mod):
    """THE regression. A helper below `if __name__ == "__main__":` does not exist
    when the module runs as a script — `python -m ...` executes top to bottom and
    calls main() first. Phase 3 shipped exactly that and crashed with NameError."""
    src = (_CLI / mod).read_text()
    guard = src.find('if __name__ == "__main__":')
    if guard == -1:
        pytest.skip(f"{mod} has no __main__ guard")
    for helper in ("_common_records", "_wheel_records"):
        m = re.search(rf"^def {helper}\(", src, re.M)
        if m:
            assert m.start() < guard, (
                f"{mod}: {helper} is defined BELOW the __main__ guard — it will "
                f"not exist when the module is run as a script")
