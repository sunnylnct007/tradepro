"""Every job module must at least COMPILE.

9 Sep 2026: preearnings_watch.py carried a syntax error — an `elif` following
an `except`, left behind when a try/except was inserted between an `if` and its
`elif`. The module could not be imported, so the WATCH framework did not run at
all for 17 hours, and the only evidence was a traceback buried in a Lambda log
that nothing was reading.

The whole suite was green throughout, because no test imported that module.

This is the cheapest possible guard: not "does it work", just "is it valid
Python". It would have caught this at CI in under a second.
"""
import py_compile
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "tradepro_strategies" / "cli"
MODULES = sorted(p for p in CLI.glob("*.py") if p.name != "__init__.py")


def test_there_are_modules_to_check():
    """A glob that silently matches nothing would make every check below pass."""
    assert len(MODULES) > 20, f"only found {len(MODULES)} CLI modules — glob wrong?"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_module_compiles(path: Path):
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        pytest.fail(f"{path.name} is not valid Python — any job that imports it "
                    f"dies at startup:\n{exc}")
