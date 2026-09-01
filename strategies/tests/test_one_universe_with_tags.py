"""One uniform list. Sub-classification by TAG, never by a second list.

Owner, 1 Sep 2026: "its good to subclassify but we shd have unirkm list", and
before that "we want a coherant and trustworthy data and not scattered data ...
as user i dont have to think many screens".

THERE WERE FOUR DEFINITIONS of "what we screen":

    1. universe/tradeable.json          244 committed names — the real definition
    2. /api/universes/{name}            DB-backed: large_50, high_beta, +12 more
    3. options_screen.DEFAULT_UNIVERSE  ~82, hand-curated INSIDE a CLI module
    4. ScreenerEndpoints.Universe       30 hardcoded ticker+conid pairs

A screen running on a different list from its neighbours is how "0 of 30" and
"21 of 82" looked like a strategy disagreement when it was a universe
disagreement — two things called "wheel", both emailing, on the same afternoon.

Phase 2 makes the 244 the single source and everything else a FILTER over it.

A NOTE ON HOW THIS WAS NEARLY BOTCHED: the first draft of the tag derivation
pasted a TRUNCATED copy of the wheel list — 20 of 82 names — into
build_universe. That would have silently shrunk what the wheel screens, which is
precisely the failure this phase exists to end, committed while ending it. The
list is now imported from one place and copied nowhere, and
`test_the_wheel_list_exists_in_exactly_one_place` pins that.
"""
from __future__ import annotations

import re
from pathlib import Path

from tradepro_strategies.universe import (
    WHEEL_SLEEVE, load_universe, universe_by_tag, universe_tags,
)
from tradepro_strategies.cli.build_universe import _apply_tags, _tag_counts

_PKG = Path(__file__).resolve().parents[1] / "tradepro_strategies"


def _rows():
    return [dict(r) for r in (load_universe(strict=False).get("symbols") or [])]


def test_every_sleeve_name_is_in_the_one_universe():
    """THE property. A sleeve is a SUBSET, not a parallel list — a name the wheel
    screens but the universe does not contain is a fifth definition."""
    universe = {r["symbol"] for r in _rows()}
    orphans = sorted(set(WHEEL_SLEEVE) - universe)
    assert not orphans, f"wheel sleeve names outside the committed universe: {orphans}"


def test_tags_are_derived_over_the_committed_universe():
    tagged = _apply_tags(_rows())
    counts = _tag_counts(tagged)
    assert counts.get("large_50") == 50, counts
    assert counts.get("wheel") == len(WHEEL_SLEEVE), counts
    assert counts.get("high_beta", 0) > 0, counts


def test_large_50_is_the_most_liquid_not_a_hand_list():
    """It has always informally meant "the big names". Derive it from median
    dollar volume so it cannot drift from what it claims to be."""
    rows = _rows()
    tagged = _apply_tags(rows)
    large = [r for r in tagged if "large_50" in r["tags"]]
    rest = [r for r in tagged
            if "large_50" not in r["tags"] and r.get("dollar_volume_median")]
    floor = min(r["dollar_volume_median"] for r in large)
    assert all(r["dollar_volume_median"] <= floor for r in rest)


def test_high_beta_tags_exactly_the_high_beta_tier():
    tagged = _apply_tags(_rows())
    for r in tagged:
        assert ("high_beta" in r["tags"]) == (r.get("beta_tier") == "high"), r["symbol"]


def test_universe_by_tag_is_empty_not_explosive_before_a_rebuild():
    """The committed file predates tags. An accessor that RAISED would break
    every screen; one that silently returned a WRONG list would be worse. Empty
    lets a caller fall back to its own list and say so."""
    assert universe_by_tag("nonexistent_tag") == []
    assert isinstance(universe_tags(), dict)


def test_the_wheel_list_exists_in_exactly_one_place():
    """Structural. A second copy is the whole defect — and was nearly
    reintroduced by the change that removed it."""
    literal = re.compile(r'"CVX"\s*,\s*"XOM"')
    holders = [f.relative_to(_PKG).as_posix()
               for f in _PKG.rglob("*.py") if literal.search(f.read_text())]
    assert holders == ["universe.py"], (
        f"the wheel sleeve is defined in more than one place: {holders}")
