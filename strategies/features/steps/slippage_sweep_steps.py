"""Steps for slippage_sweep.feature — exercises the F-1 sensitivity
helper without spinning up the full engine. The end-to-end scenario
injects a fake WalkForwardValidator that returns a deterministic
pnl = 100 - 5 * bps curve so the math is auditable."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from behave import given, then, when

from tradepro_strategies.paper.slippage_sweep import (
    SweepResult,
    SweepRow,
    classify_verdict,
    compute_break_even_bps,
    run_sweep,
)


def _rows_from_table(table) -> list[SweepRow]:
    rows: list[SweepRow] = []
    for row in table:
        rows.append(
            SweepRow(
                slippage_bps=float(row["bps"]),
                total_realised_pnl=float(row["pnl"]),
                avg_session_pnl=0.0,
                sharpe_per_session=0.0,
                max_drawdown=0.0,
                total_fills=1,  # non-zero so "broken" requires negative pnl, not zero fills
                win_session_pct=0.0,
            )
        )
    rows.sort(key=lambda r: r.slippage_bps)
    return rows


@given('a sweep result with rungs')
def step_sweep_with_rungs(context):
    context.sweep_rows = _rows_from_table(context.table)


@given('a sweep result with all zero fills')
def step_sweep_all_zero_fills(context):
    context.sweep_rows = [
        SweepRow(
            slippage_bps=b,
            total_realised_pnl=0.0,
            avg_session_pnl=0.0,
            sharpe_per_session=0.0,
            max_drawdown=0.0,
            total_fills=0,
            win_session_pct=0.0,
        )
        for b in (1.0, 5.0, 10.0)
    ]


@when('break_even_bps is computed')
def step_compute_break_even(context):
    context.break_even = compute_break_even_bps(context.sweep_rows)
    context.verdict = classify_verdict(context.sweep_rows, context.break_even)


@when('the verdict is computed')
def step_compute_verdict(context):
    context.break_even = compute_break_even_bps(context.sweep_rows)
    context.verdict = classify_verdict(context.sweep_rows, context.break_even)


@then('break_even_bps is between {lo:f} and {hi:f}')
def step_break_even_between(context, lo, hi):
    assert context.break_even is not None, "break_even_bps was None"
    assert lo <= context.break_even <= hi, (
        f"expected {lo} <= break_even <= {hi}, got {context.break_even}"
    )


@then('break_even_bps is None')
def step_break_even_none(context):
    assert context.break_even is None, (
        f"expected None, got {context.break_even}"
    )


@then('break_even_bps is approximately {value:f}')
def step_break_even_approx(context, value):
    assert context.break_even is not None, "break_even_bps was None"
    delta = abs(context.break_even - value)
    assert delta < 0.5, (
        f"expected break_even ≈ {value}, got {context.break_even} (Δ={delta})"
    )


@then('the sweep verdict is "{label}"')
def step_verdict_is(context, label):
    assert context.verdict == label, (
        f"expected verdict={label!r}, got {context.verdict!r}"
    )


# ── End-to-end scenario: fake validator ───────────────────────────


@dataclass
class _FakeResult:
    """Minimal stand-in for WalkForwardResult.to_summary path."""

    total_realised_pnl: float
    avg_session_pnl: float = 0.0
    sharpe_per_session: float = 0.0
    max_drawdown: float = 0.0
    total_fills: int = 1
    win_session_pct: float = 0.0


class _FakeValidator:
    """Drop-in for WalkForwardValidator inside run_sweep. Returns a
    pnl curve parametrised by ``slippage_bps`` so the test asserts
    the loop ran exactly N times AND the math used the right bps.

    Class-level ``calls`` lets the step assert total invocation count
    across the instances run_sweep spawns. Plain class (not dataclass)
    because run_sweep instantiates with keyword args we don't care to
    declare as fields."""

    # Class-level state — reset by the @given step before each run.
    calls: list = []
    pnl_fn = staticmethod(lambda bps: 0.0)

    def __init__(self, *, strategy_factory, symbol, capital_usd,
                 broker, interval, slippage_bps):
        self.slippage_bps = float(slippage_bps)

    async def run(self, start, end):
        _FakeValidator.calls.append(self.slippage_bps)
        pnl = _FakeValidator.pnl_fn(self.slippage_bps)
        return _FakeResult(total_realised_pnl=pnl)


@given('a fake validator that returns pnl = 100 - 5 * bps')
def step_install_fake_validator(context):
    _FakeValidator.calls = []
    _FakeValidator.pnl_fn = staticmethod(lambda bps: 100.0 - 5.0 * bps)
    context.fake_validator_cls = _FakeValidator


@when('the sweep runs across bps [{rungs}]')
def step_run_sweep(context, rungs):
    bps_list = tuple(float(x.strip()) for x in rungs.split(","))

    def factory():
        # The fake validator never touches the strategy, but run_sweep
        # still calls strategy_factory().strategy_id for aggregation
        # inside the real validator path — the fake bypasses that.
        return object()

    async def _go():
        return await run_sweep(
            strategy_factory=factory,
            symbol="TEST",
            start=date(2026, 4, 1),
            end=date(2026, 4, 5),
            bps_list=bps_list,
            validator_cls=context.fake_validator_cls,
        )

    context.sweep_result = asyncio.run(_go())
    context.break_even = context.sweep_result.break_even_bps
    context.verdict = context.sweep_result.verdict


@then('{n:d} rows are produced in ascending bps order')
def step_assert_rows(context, n):
    rows = context.sweep_result.rows
    assert len(rows) == n, f"expected {n} rows, got {len(rows)}"
    bps_seen = [r.slippage_bps for r in rows]
    assert bps_seen == sorted(bps_seen), f"rows not ascending: {bps_seen}"


@then('the validator was invoked {n:d} times')
def step_assert_calls(context, n):
    assert len(_FakeValidator.calls) == n, (
        f"expected {n} validator runs, got {len(_FakeValidator.calls)}"
    )
