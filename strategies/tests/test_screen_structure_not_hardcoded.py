"""The screen must never hardcode a trade's structure again.

A single hardcoded `Structure.CASH_SECURED_PUT` was the largest source of
refusals on the live board — 90 of 82 screened names carried a block that was
really a statement about the STRUCTURE, not the trade (40x regime, 50x
earnings; the counts overlap). The fix is `degrade_for_context`, and the fix is
worthless the moment a third call site appears without it.

ASSERTED ON THE AST, NOT BY GREP. A grep test cannot see whether the code it
matched is on the path that runs, and this desk has already paid two sessions
for a source-slice test that passed over unreachable code.
"""
import ast
import pathlib

SCREEN = (pathlib.Path(__file__).resolve().parents[1]
          / "tradepro_strategies" / "cli" / "options_screen.py")


def _trade_candidate_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "TradeCandidate"]


def _structure_kwarg(call: ast.Call) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == "structure"), None)


def test_the_screen_builds_candidates_at_all():
    # Guards the guard: if the call shape is ever renamed, the assertions below
    # would vacuously pass over zero candidates and prove nothing.
    calls = _trade_candidate_calls(ast.parse(SCREEN.read_text()))
    assert len(calls) >= 2, f"expected both screen call sites, found {len(calls)}"


def test_no_candidate_names_a_literal_structure():
    for call in _trade_candidate_calls(ast.parse(SCREEN.read_text())):
        structure = _structure_kwarg(call)
        assert structure is not None, (
            f"line {call.lineno}: TradeCandidate built with no explicit structure")
        # A bare `Structure.X` is the bug. The structure must come from a NAME
        # that something computed — `degrade_for_context`'s result, or a
        # fallback expression over it.
        assert not isinstance(structure, ast.Attribute) or \
            not (isinstance(structure.value, ast.Name)
                 and structure.value.id == "Structure"), (
            f"line {call.lineno}: structure is hardcoded to "
            f"Structure.{getattr(structure, 'attr', '?')}. Route it through "
            f"degrade_for_context so the regime and earnings fallbacks apply "
            f"on THIS path too.")


def test_every_call_site_consults_the_resolver():
    src = SCREEN.read_text()
    tree = ast.parse(src)
    resolver_calls = [n for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id == "degrade_for_context"]
    candidates = _trade_candidate_calls(tree)
    assert len(resolver_calls) >= len(candidates), (
        f"{len(candidates)} TradeCandidate sites but only {len(resolver_calls)} "
        f"degrade_for_context calls — at least one path builds a trade without "
        f"the regime/earnings fallback. That is the half-applied fix, again.")
