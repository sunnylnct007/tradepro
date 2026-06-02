"""Slippage sensitivity sweep — Phase F-1.

Re-runs the same backtest at multiple ``slippage_bps`` levels and
returns a per-bps summary + a ``break_even_bps`` derived from the
crossover point where total realised P&L flips sign. Answers the
L2 question directly: "would my strategy survive at 25bps spread?"

The sweep reuses ``WalkForwardValidator`` unchanged — for each bps
level it constructs a fresh validator and runs the same range. The
BarStore cache makes the second-through-Nth pass near-instant
(no Yahoo / IG calls).

Design notes
------------
* Stateless: ``run_sweep`` takes a strategy_factory and never mutates
  shared state. Safe to call concurrently across symbols.
* Pure-Python: no .NET / DB / migration involvement. The output is
  a plain dict ready for ``push_to_api`` or JSON dump.
* Linear interpolation for ``break_even_bps``: between the two bps
  rungs that bracket the sign flip. Returns ``None`` when the curve
  never crosses (all positive = robust; all negative = broken).
* Default rungs ``[1, 5, 10, 25, 50]`` span US ETF (≈1bp) to thin
  single-name (≈50bp). Override with ``bps_list`` for finer scans.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from .validator import StrategyFactory, WalkForwardResult, WalkForwardValidator


DEFAULT_BPS_RUNGS: tuple[float, ...] = (1.0, 5.0, 10.0, 25.0, 50.0)


@dataclass(frozen=True)
class SweepRow:
    """One row of the sensitivity table — one slippage_bps level."""

    slippage_bps: float
    total_realised_pnl: float
    avg_session_pnl: float
    sharpe_per_session: float
    max_drawdown: float
    total_fills: int
    win_session_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "slippage_bps": self.slippage_bps,
            "total_realised_pnl": round(self.total_realised_pnl, 4),
            "avg_session_pnl": round(self.avg_session_pnl, 4),
            "sharpe_per_session": round(self.sharpe_per_session, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "total_fills": self.total_fills,
            "win_session_pct": round(self.win_session_pct, 4),
        }

    @classmethod
    def from_result(cls, bps: float, result: WalkForwardResult) -> "SweepRow":
        return cls(
            slippage_bps=bps,
            total_realised_pnl=result.total_realised_pnl,
            avg_session_pnl=result.avg_session_pnl,
            sharpe_per_session=result.sharpe_per_session,
            max_drawdown=result.max_drawdown,
            total_fills=result.total_fills,
            win_session_pct=result.win_session_pct,
        )


@dataclass(frozen=True)
class SweepResult:
    """Full sweep payload. ``break_even_bps`` is the interpolated
    slippage level where total P&L crosses zero — ``None`` when the
    curve never crosses within the scanned range."""

    rows: list[SweepRow]
    break_even_bps: float | None
    verdict: str  # "robust" | "fragile" | "broken"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "break_even_bps": (
                round(self.break_even_bps, 4)
                if self.break_even_bps is not None
                else None
            ),
            "verdict": self.verdict,
        }


def compute_break_even_bps(rows: list[SweepRow]) -> float | None:
    """Linear-interpolate the bps level where total_realised_pnl
    crosses zero. Assumes ``rows`` is sorted ascending by bps.

    Returns ``None`` when all rows share a sign (no crossover within
    the scanned range — the operator should widen ``bps_list``)."""
    if len(rows) < 2:
        return None
    for prev, curr in zip(rows, rows[1:]):
        if prev.total_realised_pnl == 0:
            return prev.slippage_bps
        if curr.total_realised_pnl == 0:
            return curr.slippage_bps
        # Sign flip between rungs ⇒ interpolate.
        if (prev.total_realised_pnl > 0) != (curr.total_realised_pnl > 0):
            span_pnl = prev.total_realised_pnl - curr.total_realised_pnl
            if span_pnl == 0:
                return prev.slippage_bps
            span_bps = curr.slippage_bps - prev.slippage_bps
            frac = prev.total_realised_pnl / span_pnl
            return prev.slippage_bps + frac * span_bps
    return None


def classify_verdict(rows: list[SweepRow], break_even: float | None) -> str:
    """Three-way label for the result viewer chip.

    * ``robust``: positive P&L even at the widest bps rung.
    * ``fragile``: positive at the tightest rung, negative at the
      widest — operator should pad realistic assumptions.
    * ``broken``: negative even at the tightest rung (or zero fills).
    """
    if not rows:
        return "broken"
    if all(r.total_fills == 0 for r in rows):
        return "broken"
    if rows[-1].total_realised_pnl > 0:
        return "robust"
    if rows[0].total_realised_pnl <= 0:
        return "broken"
    return "fragile"


async def run_sweep(
    *,
    strategy_factory: StrategyFactory,
    symbol: str,
    start: date | datetime,
    end: date | datetime,
    bps_list: tuple[float, ...] | list[float] = DEFAULT_BPS_RUNGS,
    capital_usd: float = 100_000.0,
    broker: str = "yfinance",
    interval: str = "1m",
    validator_cls: type[WalkForwardValidator] = WalkForwardValidator,
) -> SweepResult:
    """Run the same backtest at every bps level and aggregate.

    Sequential by design — the BarStore cache makes the second pass
    near-free, and sequential output keeps progress readable for the
    operator watching stderr. Parallel runs would require a per-bps
    validator instance anyway, so the speedup is small."""
    sorted_bps = sorted(set(float(b) for b in bps_list))
    rows: list[SweepRow] = []
    for bps in sorted_bps:
        validator = validator_cls(
            strategy_factory=strategy_factory,
            symbol=symbol,
            capital_usd=capital_usd,
            broker=broker,
            interval=interval,
            slippage_bps=bps,
        )
        result = await validator.run(start, end)
        rows.append(SweepRow.from_result(bps, result))
    break_even = compute_break_even_bps(rows)
    verdict = classify_verdict(rows, break_even)
    return SweepResult(rows=rows, break_even_bps=break_even, verdict=verdict)


def format_table(result: SweepResult) -> str:
    """Pretty-print the sensitivity table for stderr/CLI output."""
    if not result.rows:
        return "(no rows)"
    header = (
        f"{'bps':>6}  {'pnl':>12}  {'avg':>10}  "
        f"{'sharpe':>8}  {'max_dd':>10}  {'fills':>6}  {'win%':>6}"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for row in result.rows:
        lines.append(
            f"{row.slippage_bps:>6.1f}  {row.total_realised_pnl:>12.2f}  "
            f"{row.avg_session_pnl:>10.4f}  "
            f"{row.sharpe_per_session:>8.4f}  "
            f"{row.max_drawdown:>10.2f}  "
            f"{row.total_fills:>6d}  "
            f"{row.win_session_pct * 100:>6.1f}"
        )
    lines.append(sep)
    if result.break_even_bps is not None:
        lines.append(f"  break_even_bps: {result.break_even_bps:.2f}")
    lines.append(f"  verdict: {result.verdict}")
    return "\n".join(lines)
