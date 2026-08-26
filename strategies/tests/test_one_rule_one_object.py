"""The screen, the harness and the LIVE strategy must run the SAME code object.

Owner: "no code or logic duplication, so you can't say oh it ran a diff code."

`test_rule_constants_not_duplicated` guards the NUMBERS. This guards the
FUNCTIONS, and it is the stronger statement: it asserts identity, not equality.
Two modules can hold the same value of SIGMA and still evaluate the rule
differently if either has its own copy of `entry_signal`. Identity cannot be
satisfied by a copy — only by an import.

Why this matters more here than in most codebases: forward-test gate F1 is
"signal fidelity — >=95% of live candidates match what the committed harness
produces for the same dates". If the screen, the harness and the live sleeve
are not literally executing the same function, F1 measures how carefully code
was copied between three files rather than whether the strategy works. The
whole twelve-week window then answers the wrong question.
"""
from __future__ import annotations

from tradepro_strategies.signals import mean_reversion as RULE


def test_the_live_strategy_runs_the_rule_module_itself():
    from tradepro_strategies.paper.strategies import mean_reversion_swing as live
    assert live.entry_signal is RULE.entry_signal
    assert live.exit_decision is RULE.exit_decision
    assert live.target_price is RULE.target_price
    assert live.stop_price is RULE.stop_price
    assert live.reward_risk is RULE.reward_risk
    assert live.MAX_HOLD is RULE.MAX_HOLD
    assert live.STOP_PCT is RULE.STOP_PCT


def test_the_published_screen_runs_the_rule_module_itself():
    from tradepro_strategies.cli import swing_candidates as screen
    # Identity, not equality. The screen used to import the CONSTANTS and then
    # re-implement the logic inline — recomputing the band and hardcoding
    # `/ 200` for the trend floor. Same answer, two places to change.
    assert screen.entry_signal is RULE.entry_signal
    assert screen.target_price is RULE.target_price
    assert screen.stop_price is RULE.stop_price
    assert screen.TREND_WINDOW is RULE.TREND_WINDOW
    assert screen.SIGMA is RULE.SIGMA
    assert screen.BB_WINDOW is RULE.BB_WINDOW
    assert screen.STOP_PCT is RULE.STOP_PCT
    assert screen.MAX_HOLD is RULE.MAX_HOLD


def test_the_mcp_tools_run_the_rule_module_itself():
    """The MCP surface is how the owner interrogates the strategy from a chat
    window. If it answered from its own copy, it would confirm whatever it had
    been given rather than what actually trades.

    Source-level, because MCP imports the rule inside the tool function rather
    than at module scope — which is fine (it is still the same object) but means
    there is no module attribute to compare."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "tradepro_strategies" / "mcp" / "tools.py").read_text()
    assert "from ..signals.mean_reversion import" in src
    for name in ("SIGMA", "BB_WINDOW", "STOP_PCT", "MAX_HOLD", "TREND_WINDOW"):
        assert f"\n{name} =" not in src, f"MCP restates {name}"


def test_the_graded_harness_runs_the_rule_module_itself():
    """mean_reversion_v2.py produces the numbers in the gates document. It once
    hardcoded MAX_HOLD = 10 and went on grading the OLD rule after the hold
    moved to 20 — appearing to contradict the very result that motivated the
    change. Source-level check because the harness executes on import."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "backtests" / "studies" / "mean_reversion_v2.py").read_text()
    assert "from tradepro_strategies.signals.mean_reversion import" in src
    for name in ("SIGMA", "BB_WINDOW", "STOP_PCT", "MAX_HOLD"):
        assert f"\n{name} =" not in src, f"{name} is re-stated in the graded harness"


def test_there_is_exactly_one_definition_of_entry_signal_in_the_tree():
    """A second `def entry_signal` anywhere is how the drift starts."""
    import glob
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    defs = []
    for f in glob.glob(str(root / "**" / "*.py"), recursive=True):
        if ".venv" in f:
            continue
        for ln, line in enumerate(Path(f).read_text(errors="replace").split("\n"), 1):
            if re.match(r"\s*def entry_signal\b", line):
                defs.append(f"{Path(f).relative_to(root)}:{ln}")
    assert defs == ["tradepro_strategies/signals/mean_reversion.py:84"] or len(defs) == 1, \
        f"entry_signal is defined in more than one place: {defs}"


def test_no_swing_module_hardcodes_the_trend_window_in_a_slice():
    """The one the constant-scanner could not see.

    The screen computed its trend floor as `sum(c[i-199:i+1]) / 200` — the
    value of TREND_WINDOW, inline, where no scan for `TREND_WINDOW = 200` would
    ever find it. Standing lesson in this repo: grep the VALUE, not the name.

    Scoped to the SWING consumers on purpose. Momentum is a different strategy
    that legitimately owns its own 200-day floor, and a check that flags it
    would be a check people learn to ignore. Strings are stripped before
    testing for the same reason: a display line reading "close / 200-SMA" is
    explaining the rule to a human, not computing it, and the first version of
    this test flagged exactly that.
    """
    import io
    import re
    import tokenize
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    swing_consumers = [
        "tradepro_strategies/cli/swing_candidates.py",
        "tradepro_strategies/paper/strategies/mean_reversion_swing.py",
    ]
    offenders = []
    for rel in swing_consumers:
        f = root / rel
        if not f.exists():
            continue
        src = f.read_text(errors="replace")
        # Tokenise so strings and comments are excluded properly rather than
        # by regex — the naive version could not tell code from prose.
        code_lines = {}
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            code_lines.setdefault(tok.start[0], []).append(tok.string)
        for ln, toks in code_lines.items():
            joined = " ".join(toks)
            if "TREND_WINDOW" in joined:
                continue
            if re.search(r"i - 199", joined) or re.search(r"/ 200\b", joined):
                offenders.append(f"{rel}:{ln}  {joined[:70]}")
    assert not offenders, (
        "the Swing trend window is hardcoded instead of imported:\n"
        + "\n".join(offenders))
