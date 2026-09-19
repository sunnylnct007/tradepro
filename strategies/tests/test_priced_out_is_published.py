"""A filter must publish what it removed, not only print it.

The breakeven filter (PR #156) drops candidates that need a higher win rate
than the strategy achieves — roughly 5 a day, mostly ETFs. It printed the
rejections to the terminal and its own commit message argued that a silent
filter "cannot be told apart from a broken scan".

It then did exactly that on the UI side: the artifact carried only the
survivors, so the desk showed a shorter list with no account of what left it.
Fixed here — `priced_out` and the bar it was judged against now travel with the
artifact, so a symbol's absence can be explained instead of inferred.
"""
import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "swing_candidates.py")


def _fn(name: str) -> str:
    src = SRC.read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name}() not found")


def test_the_artifact_carries_the_rejected_rows():
    body = _fn("build_artifact")
    assert '"priced_out"' in body, (
        "the artifact does not publish priced_out — the screen cannot explain "
        "why a name it expected is missing"
    )


def test_the_artifact_carries_the_bar_they_were_judged_against():
    # A rejection without its threshold is an assertion, not evidence.
    assert '"breakeven_max_win_pct"' in _fn("build_artifact")


def test_a_rejected_row_keeps_the_numbers_that_justify_it():
    body = _fn("build_artifact")
    for field in ("breakeven_win_pct", "reward_risk", "target_pct", "symbol"):
        assert field in body, f"priced_out rows drop {field}, so the reason cannot be shown"


def test_the_caller_actually_passes_it_through():
    """A parameter nobody supplies is the same as no parameter."""
    src = SRC.read_text()
    main = _fn("main")
    assert "priced_out=priced_out" in main, (
        "build_artifact accepts priced_out but main() never passes it — the "
        "field would publish as an empty list forever"
    )
    assert "rows, quarantined, near, priced_out = scan(" in main
