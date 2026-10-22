"""A 642-name screen must not run into the next session.

The universe went 82 -> 642 on 22 Oct 2026 and this loop had no deadline. At
the 11s/symbol measured in-session that is ~2 hours, which is fine. At the
75s/symbol measured PRE-OPEN — G3 cannot read spot, every name burns two 22s
retry cycles — it is thirteen hours.

Not hypothetical: the capture lane carries MAX_RUN_S because one of its runs
took 22 hours and ate a day unnoticed.
"""
import ast
import inspect
import pathlib

SCREEN = (pathlib.Path(__file__).resolve().parents[1]
          / "tradepro_strategies" / "cli" / "options_screen.py")


def _run_screen_src() -> str:
    tree = ast.parse(SCREEN.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_screen")
    return ast.get_source_segment(SCREEN.read_text(), fn) or ""


def test_the_screen_loop_has_a_deadline():
    src = _run_screen_src()
    assert "TRADEPRO_SCREEN_MAX_RUN_S" in src, (
        "the per-symbol loop has no wall-clock budget — at 75s/symbol a "
        "642-name universe runs for thirteen hours")


def test_the_budget_is_configurable_not_hardcoded():
    from tradepro_strategies.cli import options_screen as osc
    src = inspect.getsource(osc.run_screen)
    assert "os.environ.get(\"TRADEPRO_SCREEN_MAX_RUN_S\"" in src


def test_a_truncated_run_publishes_what_it_has():
    # Publishing nothing is what this desk actually lived through: two days
    # where the screen ran, failed at the very end, and left a board dated
    # 19 Oct. A partial board that says it is partial is strictly better.
    src = _run_screen_src()
    assert "break" in src, "the budget must stop the loop, not raise"
    assert "truncated" in src


def test_not_reached_is_not_reported_as_refused():
    # The distinction that makes a partial board safe to read. A name that was
    # never screened has not been judged, and a board that blurs the two is
    # exactly the false-clear this desk keeps removing.
    src = _run_screen_src()
    assert "not_reached" in src
    assert "not refusals" in src or "NOT refusals" in src
