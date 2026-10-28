"""The screen must finish before the machine sleeps.

THE 27 OCT RUN: started 16:01, finished 08:49 the next morning — 16h48m — with
a 3-hour budget that never fired once.

time.monotonic() does not advance while macOS sleeps, and this Mac sleeps
through the night (pmset: Sleep/DarkWake cycles from 00:06, battery at 3%). The
budget was measuring AWAKE time, so ~3h of real work spread over a night never
reached the limit.

That is also the real cause of the "DNS" publish failures: the run finished in
a different network state from the one it started in. Four boards went that way
in a week.
"""
import ast
import pathlib

SCREEN = (pathlib.Path(__file__).resolve().parents[1]
          / "tradepro_strategies" / "cli" / "options_screen.py")


def _run_screen_src() -> str:
    txt = SCREEN.read_text()
    fn = next(n for n in ast.walk(ast.parse(txt))
              if isinstance(n, ast.FunctionDef) and n.name == "run_screen")
    return ast.get_source_segment(txt, fn) or ""


def test_the_budget_uses_wall_clock_not_monotonic():
    """THE BUG. monotonic() stops counting while the host sleeps, so a budget
    built on it cannot bound a run that straddles the night — which is exactly
    the run it needs to bound."""
    src = _run_screen_src()
    assert "_time_mod.monotonic()" not in src, (
        "the budget is measuring awake time; a run across an overnight sleep "
        "will never reach it (27 Oct: 16h48m against a 3h limit)")
    assert "_time_mod.time()" in src


def test_the_universe_is_capped_for_runtime():
    from tradepro_strategies.cli.wheel_universe import MAX_NAMES
    assert 60 <= MAX_NAMES <= 120, (
        f"MAX_NAMES={MAX_NAMES}: at ~180s/symbol the run must fit inside the "
        f"evening, before the Mac sleeps")


def test_the_sleeve_actually_respects_the_cap():
    from tradepro_strategies.cli.wheel_universe import MAX_NAMES
    from tradepro_strategies.universe import WHEEL_SLEEVE
    assert len(WHEEL_SLEEVE) <= MAX_NAMES + 20, (
        f"{len(WHEEL_SLEEVE)} names against a cap of {MAX_NAMES} (+ETFs) — "
        f"re-run `tradepro-wheel-universe --write` and commit")
