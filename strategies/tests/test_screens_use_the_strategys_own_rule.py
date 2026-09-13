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


def test_the_watcher_closes_on_each_strategys_own_max_hold():
    """signal_watch CLOSES paper positions — its horizons must be the rules'.

    These were written out as {swing: 20, momentum: 60}. Both matched at the
    time, which is precisely how a copied constant hides: correct until the
    day someone tunes the rule, after which the watcher goes on exiting at the
    OLD horizon. It does not merely report "held too long" — it closes the
    position, so a stale number here silently exits trades at the wrong place
    and the forward-test record then measures a holding period the strategy
    never had.
    """
    from tradepro_strategies.cli.signal_watch import _max_hold_sessions
    from tradepro_strategies.signals.mean_reversion import MAX_HOLD as SWING
    from tradepro_strategies.cli.momentum_candidates import MAX_HOLD as MOM

    got = _max_hold_sessions()
    assert got["candidates_swing"] == SWING, (
        f"the watcher would close swing positions at {got['candidates_swing']} "
        f"sessions while the rule says {SWING}"
    )
    assert got["candidates_momentum"] == MOM, (
        f"the watcher would close momentum positions at "
        f"{got['candidates_momentum']} sessions while the rule says {MOM}"
    )


def test_no_screen_restates_a_strategys_holding_horizon():
    """The horizons are imported, never typed. Catches the next copy."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if "/tests/" in rel or rel.startswith("signals/"):
            continue
        if rel in ("cli/momentum_candidates.py",):      # momentum OWNS its 60
            continue
        src = path.read_text(encoding="utf-8")
        for lit in ('"candidates_swing": 20', "'candidates_swing': 20",
                    '"candidates_momentum": 60', "'candidates_momentum': 60"):
            if lit in src:
                offenders.append(f"{rel}  →  {lit}")
    assert not offenders, (
        "a strategy's holding horizon is written out instead of imported:\n  "
        + "\n  ".join(offenders)
        + "\n\nImport MAX_HOLD from the strategy. This value decides when a "
          "live paper position is CLOSED."
    )


def test_no_rows_why_text_names_a_strategy_that_did_not_pick_it():
    """The rationale on a row must belong to the rule that selected it.

    Momentum rows carried why="Ichimoku, above cloud" — a rationale from a
    different strategy entirely. Momentum has no cloud, no tenkan, no kijun:
    it fires when a name in an uptrend pulls back TO its 10-day average. The
    row was describing a test it never ran, which is the same defect as the
    Setups screen's "engine: BUY" label, one layer cheaper to make and just
    as misleading to read.
    """
    import re
    ICHIMOKU_WORDS = re.compile(r"ichimoku|above cloud|kijun|tenkan", re.I)
    # Modules that legitimately talk about the cloud because they RUN it.
    ICHIMOKU_OWNERS = ("cli/today_setups.py", "paper/strategies/ichimoku",
                       "paper/strategies/_equity_trader_signal.py",
                       "paper/strategies/_fx_trader_signal.py")

    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if "/tests/" in rel or any(rel.startswith(o) for o in ICHIMOKU_OWNERS):
            continue
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):        # comments may discuss anything
                continue
            if "why" in line and ICHIMOKU_WORDS.search(line):
                offenders.append(f"{rel}:{ln}  {stripped[:80]}")

    assert not offenders, (
        "a row's why-text names Ichimoku in a module that does not run it:\n  "
        + "\n  ".join(offenders)
        + "\n\nState the rule that actually selected the row."
    )
