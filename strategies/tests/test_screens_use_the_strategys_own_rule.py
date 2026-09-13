"""A screen that names a strategy must run THAT strategy's rule.

THE FAILURE THIS LOCKS OUT, because it reached the owner as trading guidance:

Today's Setups labelled every row `engine: BUY, above cloud, at the kijun`,
which reads as the ichimoku_equity sleeve — the one strategy here with a
validated record. It never ran that sleeve's rule. Its verdict was
`market_state.entry_signal` (an RSI + 200-SMA judgement; market_state
computes no tenkan and no kijun at all) combined with the screen's own cloud
check and a textbook 26-period kijun, where the strategy runs 32.

ichimoku_equity's entry is `Close > cloud_top AND tenkan(5) > kijun(32)`.

On 13 Sep 2026 ABBV was starred on that board while the SAME symbol sat in
the portfolio panel as "SIGNAL SAYS SELL, STILL HELD" — its tenkan had
crossed below its kijun, which is that strategy's EXIT. One screen, two
verdicts, one name. Across the store, 23% of rows meeting the screen's entry
also satisfied the strategy's exit at that moment.

The repo already guards duplicated CONSTANTS (test_one_rule_one_object caught
a hardcoded 200-day window). Nothing guarded a duplicated RULE, which is the
more expensive of the two: a constant drifts by a number, a rule drifts by a
verdict. These two tests close that.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

SRC = Path(__file__).resolve().parents[1] / "tradepro_strategies"

# The one place the Ichimoku lines may be defined. Everything else imports it.
CANONICAL = "paper/strategies/_equity_trader_signal.py"

# The FX sleeve is a DIFFERENT strategy running textbook 9/26/52, not a copy
# of the equity 5/32/50. One rule per strategy, one object — not one Ichimoku
# in the repository. It defines its own lines legitimately.
ALLOWED_OWN_DEFINITION = {CANONICAL, "paper/strategies/_fx_trader_signal.py"}


def _frame(sym: str) -> pd.DataFrame | None:
    import sys
    sys.path.insert(0, str(SRC.parent))
    from tradepro_strategies.cli.build_universe import _load
    try:
        return _load(sym)
    except Exception:  # noqa: BLE001 — a missing symbol is not this test's business
        return None


@pytest.mark.parametrize("sym", ["ABBV", "PFE", "ANET", "FIVE", "AAPL"])
def test_the_setups_screen_agrees_with_the_sleeve_it_names(sym):
    """Behavioural parity: same entry verdict as the strategy, per symbol.

    Stronger than checking the import, because it survives a refactor: if
    anyone re-derives the lines by hand again, the numbers will drift and
    this fails on the drift rather than on the style.
    """
    df = _frame(sym)
    if df is None or len(df) < 120:
        pytest.skip(f"{sym}: not enough store history")

    from tradepro_strategies.paper.strategies._equity_trader_signal import (
        compute_indicators)

    ren = df.rename(columns={"high": "High", "low": "Low", "close": "Close"})
    ich = compute_indicators(ren)
    tenkan = float(ich["tenkan"].iloc[-1])
    kijun = float(ich["kijun"].iloc[-1])
    strategy_says_buyable = tenkan > kijun

    from tradepro_strategies.cli import today_setups as TS
    row = TS._setup_for(df)
    assert row is not None, f"{sym}: the screen produced no row to compare"

    assert row.get("strategy_entry_ok") == strategy_says_buyable, (
        f"{sym}: the Setups screen says strategy_entry_ok="
        f"{row.get('strategy_entry_ok')} while ichimoku_equity's own tenkan "
        f"{tenkan:.2f} vs kijun {kijun:.2f} says {strategy_says_buyable}. "
        "The screen has drifted from the rule it names — this is the ABBV bug."
    )
    if not strategy_says_buyable:
        assert row.get("classification") != "consider", (
            f"{sym}: starred as a setup while the named strategy would not buy "
            "it. A star on a row the strategy would SELL is the screen arguing "
            "with itself."
        )


def test_nobody_re_derives_the_ichimoku_lines_by_hand():
    """The lines are defined ONCE. Everyone else imports that definition.

    Catches the shape before it can produce a wrong verdict: a rolling max/min
    midpoint over a tenkan/kijun-sized window, written anywhere but the
    canonical module.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED_OWN_DEFINITION or "/tests/" in rel:
            continue
        src = path.read_text(encoding="utf-8")
        if "tenkan" not in src and "kijun" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:            # not our problem here
            continue
        # An assignment whose target mentions tenkan/kijun AND whose value does
        # its own max/min arithmetic is a re-derivation, not a read.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = " ".join(
                t.id if isinstance(t, ast.Name)
                else getattr(t, "attr", "") if isinstance(t, ast.Attribute)
                else "" for t in targets).lower()
            if "tenkan" not in names and "kijun" not in names:
                continue
            value_src = ast.dump(node.value)
            if ("max" in value_src and "min" in value_src) or "rolling" in value_src:
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "the Ichimoku lines are re-derived outside "
        f"{CANONICAL}:\n  " + "\n  ".join(offenders)
        + "\n\nImport compute_indicators instead. A second copy stops matching "
          "the day someone changes the periods, and the screen then reports a "
          "verdict the strategy does not hold."
    )
