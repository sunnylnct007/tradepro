"""The bus cap must never silently hide part of the universe.

TWICE NOW. On 25 Aug a literal 170 was dropping 74 of swing's 244 names, and
the fix recorded in the source was that the cap "now defaults to the SIZE OF
THE UNIVERSE WE TRADE rather than a literal that goes stale the next time the
universe moves". The comment said that; the code kept a literal, 400.

On 24 Sep the universe was 917 and 517 names — 56% — were never evaluated for
entry, including GEN, which the published swing screen listed as a candidate
that same session. A forward test whose F1 gate is "live candidates match the
committed harness" cannot be run on 44% of the harness's universe.

This asserts the PROPERTY (no silent truncation by default), not the number,
because the number is what went stale both times.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "paper_session.py").read_text()


def test_the_cap_default_is_derived_not_a_literal():
    """Assert on the ast TREE, not on a regex over source text.

    A grep test cannot see what the code MEANS — this desk has a standing note
    about exactly that. The assignment is parsed and the default expression
    inspected, so reformatting the line cannot break the test and a literal
    cannot sneak past it.
    """
    import ast

    tree = ast.parse(SRC)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "_BUS_SYMBOL_CAP" not in names:
            continue
        found.append(ast.unparse(node.value))
    assert found, "the bus cap assignment could not be found — renamed?"
    expr = found[0]
    assert "len(symbols)" in expr, (
        f"the bus cap default is {expr!r} — a literal goes stale the next time "
        "the universe moves, which has now happened twice (170 in Aug, 400 in "
        "Sep)")
    # and no bare integer fallback lurking beside it
    default_nums = [n.value for n in ast.walk(ast.parse(expr))
                    if isinstance(n, ast.Constant) and isinstance(n.value, int)]
    assert not default_nums, (
        f"a literal {default_nums} remains in the cap default — that is the bug")


def test_truncation_is_still_reported_loudly_when_it_happens():
    """An explicit throttle is fine. A SILENT one is the defect."""
    assert "BUS CAP:" in SRC
    assert "coverage loss, not a filter" in SRC
    idx = SRC.index("BUS CAP:")
    window = SRC[max(0, idx - 400):idx]
    assert "log.error" in window, (
        "dropping names from the bus must be logged at ERROR — it is a "
        "correctness problem, not information")


def test_the_universe_is_not_truncated_by_default():
    """The behavioural claim, exercised rather than read."""
    symbols = [f"SYM{i}" for i in range(917)]
    import os
    env = os.environ.get("TRADEPRO_BUS_SYMBOL_CAP")
    cap = int(env) if env else len(symbols)
    assert cap >= len(symbols), (
        f"cap {cap} < universe {len(symbols)} — names would be dropped with no "
        "operator having asked for it")
