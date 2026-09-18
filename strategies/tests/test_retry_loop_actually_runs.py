"""The placement retry must be REACHABLE code, not dead code.

WHAT HAPPENED. PR #149 added a retry around the strangle placement pass. To
avoid a NameError I moved `def _finish(...)` above the retry block — and
de-indented it from 8 spaces to 4 in the process, which closed the enclosing
`if args.place:` block. Everything after it at 8 spaces, the ENTIRE retry loop,
silently became the body of `_finish`.

`_finish` is only ever called FROM the retry loop. So nothing invoked either
one. Placement stopped executing altogether.

It failed silently and perfectly:

    run_log        status=ok, no error
    decision log   16 rows persisted every day, as normal
    placed         NULL   (not False — never written)
    shadow         NULL
    place_error    NULL   (no failure, because nothing was attempted)

    date        rows  placed=True  placed=NULL  with place_error
    2026-09-15    16            3            0               13
    2026-09-16    16            0            0               16   <- all failed, all RECORDED
    2026-09-17    16            0           16                0   <- silent
    2026-09-18    16            0           16                0   <- silent

Two full sessions placed nothing, and this is worse than it sounds: the vol
gate declines essentially every US session, so SHADOW fills are the only thing
feeding the funding reliability gates (S1/S2/S3/B2/B3). The evidence clock had
stopped and nothing said so.

WHY THE ORIGINAL TESTS MISSED IT. They asserted on SLICES OF SOURCE TEXT —
"is this string present between these markers". The strings were all present.
They were just unreachable. A test that greps cannot tell live code from dead
code, so these assert on the parsed STRUCTURE instead.
"""
import ast
import pathlib

import pytest

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "index_strangle_paper.py")


def _tree():
    return ast.parse(SRC.read_text())


def _main():
    for n in ast.walk(_tree()):
        if isinstance(n, ast.FunctionDef) and n.name == "main":
            return n
    pytest.fail("main() not found in index_strangle_paper.py")


def _finish_def():
    for n in ast.walk(_tree()):
        if isinstance(n, ast.FunctionDef) and n.name == "_finish":
            return n
    pytest.fail("_finish() not found")


def test_the_retry_loop_is_not_swallowed_by_finish():
    """The exact 17-18 Sep bug: the retry loop nested inside _finish."""
    src = SRC.read_text()
    seg = ast.get_source_segment(src, _finish_def()) or ""
    assert "while pending" not in seg, (
        "The retry loop is INSIDE _finish(). _finish is only called from that "
        "loop, so neither ever runs and placement is dead code. Check the "
        "indentation of `def _finish` — it belongs inside `if args.place:`, "
        "at the same level as `units` and the retry block."
    )
    assert "RETRY_BUDGET_S" not in seg, "retry budget defined inside _finish"


def test_finish_and_the_retry_loop_are_siblings():
    """Both must live in the SAME block, so neither can capture the other."""
    def walk(body, depth=0):
        """Yield (statement, parent_body) pairs."""
        for st in body:
            yield st, body
            for attr in ("body", "orelse", "finalbody"):
                inner = getattr(st, attr, None)
                if isinstance(inner, list):
                    yield from walk(inner, depth + 1)

    finish_parent = retry_parent = None
    for st, parent in walk(_main().body):
        if isinstance(st, ast.FunctionDef) and st.name == "_finish":
            finish_parent = id(parent)
        if isinstance(st, ast.While):
            # the retry loop is the `while pending:` one
            if isinstance(st.test, ast.Name) and st.test.id == "pending":
                retry_parent = id(parent)

    assert finish_parent is not None, "_finish is not defined inside main()"
    assert retry_parent is not None, "the `while pending:` retry loop is missing"
    assert finish_parent == retry_parent, (
        "_finish and the retry loop are in DIFFERENT blocks. They must be "
        "siblings — when they are not, one ends up nested in the other and "
        "the whole placement path becomes unreachable (17-18 Sep 2026)."
    )


def test_the_retry_loop_is_reachable_from_the_place_branch():
    """It must sit under `if args.place:`, not under a def."""
    for st in _main().body:
        if not isinstance(st, ast.If):
            continue
        # if args.place:
        t = st.test
        if isinstance(t, ast.Attribute) and t.attr == "place":
            names = [s.test.id for s in st.body
                     if isinstance(s, ast.While) and isinstance(s.test, ast.Name)]
            assert "pending" in names, (
                "`if args.place:` does not directly contain the `while pending:` "
                "retry loop — placement will not run."
            )
            return
    pytest.fail("no `if args.place:` branch found in main()")


def test_finish_is_called_from_outside_itself():
    """A function only called from its own body is dead code."""
    src = SRC.read_text()
    fin = _finish_def()
    inner = set(range(fin.lineno, fin.end_lineno + 1))
    calls_outside = [
        n.lineno for n in ast.walk(_tree())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_finish" and n.lineno not in inner
    ]
    assert calls_outside, (
        "_finish() is never called from outside its own body — it is dead code."
    )
