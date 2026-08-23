"""No module may quietly redefine a constant that belongs to THE Swing rule.

`signals/mean_reversion.py` is the single definition of the mean-reversion
rule; the screen, the backtest harness and the live strategy all import from
it. On 2026-08-23 the hold was changed from 10 sessions to 20, and two copies
of `MAX_HOLD = 10` survived elsewhere — one in the backtest harness, one in the
screen. The harness went on grading the OLD rule and so appeared to *contradict*
the very result that motivated the change, and the screen would have published
"exit by 10 sessions" on every row while the live strategy held for 20.

That is the failure mode this repo produces most often, and it never announces
itself as an error: two components quietly disagree, and whichever you happen
to read becomes what you believe. Four instances surfaced in a single day
across both lanes.

A blanket "same name defined twice" check is too noisy to gate on — module
private names (`_TIMEOUT`, `_PROMPT`) collide harmlessly all over the tree, and
a check that cries wolf gets ignored, which is its own bug. So this test is
narrow on purpose: it guards the constants of one module that is explicitly
load-bearing, and every legitimate redefinition must be written down here with
a reason rather than merely tolerated.

If this test fails, the fix is almost always to IMPORT the constant rather than
restate it. Add an ALLOWED entry only when the other module genuinely owns a
different number — a different strategy, not a different opinion about the same
strategy.
"""
from __future__ import annotations

import ast
from pathlib import Path

STRATEGIES_ROOT = Path(__file__).resolve().parents[1]
RULE = STRATEGIES_ROOT / "tradepro_strategies" / "signals" / "mean_reversion.py"

# Trees that can plausibly hold a stale copy of the rule.
SCAN_ROOTS = [
    STRATEGIES_ROOT / "tradepro_strategies",
    STRATEGIES_ROOT / "backtests",
]

# (module path suffix, constant) -> why this separate definition is legitimate.
ALLOWED = {
    ("cli/momentum_candidates.py", "MAX_HOLD"):
        "Momentum is a different strategy and owns its own hold length "
        "(60 sessions vs Swing's 20) — not a copy of the Swing rule's value.",
    ("cli/momentum_candidates.py", "STOP_PCT"):
        "Momentum owns its own stop: a hard -8% from entry PLUS an 8% trailing "
        "stop from the peak, documented in its module header. The 0.08 happens "
        "to equal Swing's and must NOT be imported from the Swing rule — if "
        "Swing's stop ever moves, Momentum's must not move with it.",
}


def _module_constants(path: Path) -> dict[str, object]:
    """Module-level UPPER_CASE assignments with literal values."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return {}
    out: dict[str, object] = {}
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id.isupper():
                try:
                    out[t.id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    pass
    return out


def test_rule_module_exists():
    assert RULE.exists(), f"the canonical rule module is missing: {RULE}"


def test_no_module_redefines_a_swing_rule_constant():
    canonical = _module_constants(RULE)
    assert canonical, f"no constants parsed from {RULE.name} — has it moved?"

    offenders: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == RULE.resolve():
                continue
            rel = path.relative_to(STRATEGIES_ROOT).as_posix()
            for name, value in _module_constants(path).items():
                if name not in canonical:
                    continue
                if any(rel.endswith(suffix) and name == const
                       for (suffix, const) in ALLOWED):
                    continue
                verdict = ("SAME value — a copy that will silently drift"
                           if value == canonical[name]
                           else f"DIFFERENT value — already disagrees "
                                f"(rule={canonical[name]!r}, here={value!r})")
                offenders.append(f"  {rel}: {name} = {value!r}  [{verdict}]")

    assert not offenders, (
        "These modules define a constant owned by signals/mean_reversion.py:\n"
        + "\n".join(offenders)
        + "\n\nImport it from the rule instead of restating it. A copy holding "
          "the same value today is not safe — that is exactly how MAX_HOLD=10 "
          "outlived the change to 20 and kept the harness grading the old rule."
    )
