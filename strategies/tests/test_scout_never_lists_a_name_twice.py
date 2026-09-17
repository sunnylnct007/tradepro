"""A name may appear on the scout board once, under ONE lens.

16 Sep 2026: the board showed GE and BA twice each, identical text, all day.
The log said it plainly every tick — `scout: 5 new-name candidate(s): GE, BA,
COST, GE, BA` — and nobody read it; the owner found it on the screen.

Cause: the generic ranked slice excluded the mover and earnings lenses but not
the contract lens, which was added later. Contract names were therefore taken
once by the generic slice and again by their own ranked line. The exclusion
list now lives in SCOUT_LENS_KEYS, and this test fails if a future lens is
appended to the ranked lists without being declared there.
"""
import pytest

from tradepro_strategies.cli.preearnings_watch import (
    SCOUT_LENS_KEYS, _select_scout_keep,
)


def _syms(keep):
    return [h["sym"] for h in keep]


def test_a_contract_name_is_listed_once_not_twice():
    hits = [
        {"sym": "GE", "contracts": {"ratio": 31.4}},
        {"sym": "BA", "contracts": {"ratio": 10.5}},
        {"sym": "COST", "earnings": ("2026-09-24", "amc", 8)},
    ]
    keep = _select_scout_keep(hits, 5)
    assert _syms(keep) == ["COST", "GE", "BA"]      # earnings first, then ratio order
    assert len(_syms(keep)) == len(set(_syms(keep)))


def test_no_lens_can_produce_a_duplicate():
    """One hit of every shape at once — still one row each."""
    hits = [
        {"sym": "GE", "contracts": {"ratio": 31.4}},
        {"sym": "NVDA", "n_app": 4},
        {"sym": "COST", "earnings": ("2026-09-24", "amc", 8)},
        {"sym": "PLAIN", "ret13w": 12.0},
    ]
    got = _syms(_select_scout_keep(hits, 5))
    assert sorted(got) == ["COST", "GE", "NVDA", "PLAIN"]


def test_the_generic_slice_respects_top_n_but_typed_lenses_are_never_capped():
    hits = [{"sym": f"G{i}", "ret13w": float(i)} for i in range(9)]
    hits += [{"sym": "GE", "contracts": {"ratio": 31.4}},
             {"sym": "BA", "contracts": {"ratio": 10.5}}]
    keep = _syms(_select_scout_keep(hits, 3))
    assert keep[:3] == ["G0", "G1", "G2"]     # generic capped
    assert "GE" in keep and "BA" in keep      # typed lenses survive the cap
    assert len(keep) == len(set(keep))


def test_contract_ratio_orders_the_contract_lens():
    hits = [{"sym": "LOW", "contracts": {"ratio": 11.0}},
            {"sym": "HIGH", "contracts": {"ratio": 40.0}}]
    assert _syms(_select_scout_keep(hits, 5)) == ["HIGH", "LOW"]


def test_a_missing_ratio_does_not_explode_the_sort():
    """A null ratio sorts last rather than raising — the feed omits it."""
    hits = [{"sym": "A", "contracts": {"ratio": None}},
            {"sym": "B", "contracts": {"ratio": 12.0}}]
    assert _syms(_select_scout_keep(hits, 5)) == ["B", "A"]


def test_an_empty_lens_payload_falls_through_to_the_generic_slice():
    """Documented, not asserted-into-existence: `{}` is falsy, so a hit whose
    lens payload is empty is ranked generically instead of by that lens. It is
    still listed exactly once, which is the invariant that matters."""
    hits = [{"sym": "A", "contracts": {}}]
    assert _syms(_select_scout_keep(hits, 5)) == ["A"]


@pytest.mark.parametrize("key", SCOUT_LENS_KEYS)
def test_every_declared_lens_key_is_excluded_from_the_generic_slice(key):
    """THE GUARD. Declare a lens in SCOUT_LENS_KEYS and it cannot double-list.

    Forget to declare it and this test cannot save you — which is why the
    ranked appends read their hits from the same key names.
    """
    probe = {"n_app": 3, "earnings": ("2026-09-24", "amc", 8),
             "contracts": {"ratio": 12.0}}[key]
    hits = [{"sym": "X", key: probe}]
    keep = _select_scout_keep(hits, 5)
    assert _syms(keep) == ["X"], f"lens {key!r} double-listed its name"
