"""WalkForwardValidator — loop the paper engine across a date range,
aggregate per-session results.

Service boundary: the validator owns "given a strategy factory + a
date range + a symbol, tell me how the strategy would have performed
day by day". It composes the engine + sources + brokers we already
built; doesn't reach inside any of them. Adding regime conditioning
(only trade trending days, etc.) is a wrapper around the per-session
loop — no engine changes needed.

Why a "factory" rather than a strategy instance:
  Every session needs a FRESH strategy with clean state. Even though
  `on_session_start` clears _state, holding a reference to one
  instance across sessions risks leaking — and the test signal we
  want is "would this strategy spec earn money over time", not "would
  this specific Python object". So the validator takes a 0-arg
  callable that returns a new instance per session.

Equity curve semantics:
  Each session's `realised_pnl` is treated as an independent draw.
  Cumulative equity = sum of per-session realised_pnl. Unrealised
  carry is NOT tracked across sessions because ORB / intraday
  strategies flatten at close — overnight holds are out of scope.
  Multi-day-hold strategies (when they arrive) will need a different
  validator that carries positions across the day boundary.

Point-in-time replay: the validator drives the SAME engine + bus +
router as a live session. Cache hits make re-runs cheap; the
deterministic ReplayBarBus under the hood guarantees that the same
input bars produce the same output P&L. That's "if I had run this
strategy starting on date X, what would have happened by date Y".
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from .engine import Engine
from .profiles import build_session
from .strategy import Strategy


log = logging.getLogger("tradepro.paper.validator")


@dataclass
class SessionResult:
    """One day's outcome. Slim by design — operators get the full
    Ledger snapshot if they need positions / commissions / individual
    fills, while aggregation runs off the summary fields here."""
    session_date: date
    realised_pnl: float
    unrealised_pnl: float
    fills_count: int
    commission_paid: float
    snapshot: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class WalkForwardResult:
    """Aggregate across N sessions. Numbers are reported in the
    strategy's quote currency (USD for US equities)."""
    strategy_id: str
    symbol: str
    sessions: list[SessionResult]
    total_realised_pnl: float
    total_fills: int
    total_commission: float
    avg_session_pnl: float
    stdev_session_pnl: float
    win_session_pct: float           # % of sessions with realised > 0
    sharpe_per_session: float        # avg / stdev; not annualised
    max_drawdown: float              # peak-to-trough on equity curve
    best_session: SessionResult | None
    worst_session: SessionResult | None
    equity_curve: list[tuple[date, float]]

    def to_summary(self) -> dict:
        """Compact dict for JSON output / CLI display. Drops the
        per-session snapshots so a 250-session backtest doesn't print
        50 MB of nested dicts."""
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "session_count": len(self.sessions),
            "total_realised_pnl": round(self.total_realised_pnl, 4),
            "total_fills": self.total_fills,
            "total_commission": round(self.total_commission, 4),
            "avg_session_pnl": round(self.avg_session_pnl, 4),
            "stdev_session_pnl": round(self.stdev_session_pnl, 4),
            "win_session_pct": round(self.win_session_pct, 4),
            "sharpe_per_session": round(self.sharpe_per_session, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "best_session": {
                "date": self.best_session.session_date.isoformat(),
                "realised_pnl": round(self.best_session.realised_pnl, 4),
            } if self.best_session else None,
            "worst_session": {
                "date": self.worst_session.session_date.isoformat(),
                "realised_pnl": round(self.worst_session.realised_pnl, 4),
            } if self.worst_session else None,
            "equity_curve": [
                (d.isoformat(), round(v, 4)) for d, v in self.equity_curve
            ],
        }


StrategyFactory = Callable[[], Strategy]


@dataclass
class WalkForwardValidator:
    """Per-session loop + aggregator. Build once, call `run()` to
    drive a backtest across a date range.

    Trading days: business days only (Mon–Fri) — US market holidays
    are skipped implicitly because Yahoo returns no bars for them and
    the engine treats empty-bar sessions as no-op.
    """

    strategy_factory: StrategyFactory
    symbol: str
    capital_usd: float = 100_000.0
    broker: str = "yfinance"
    interval: str = "1m"
    slippage_bps: float = 5.0
    pace_seconds: float | str | None = None

    async def run(
        self,
        start_date: date | datetime,
        end_date: date | datetime,
    ) -> WalkForwardResult:
        sessions: list[SessionResult] = []
        start_d = start_date.date() if isinstance(start_date, datetime) else start_date
        end_d = end_date.date() if isinstance(end_date, datetime) else end_date
        if end_d < start_d:
            raise ValueError(f"end_date {end_d} is before start_date {start_d}")

        for d in _business_days(start_d, end_d):
            log.info("session %s · %s", self.symbol, d.isoformat())
            try:
                result = await self._run_one_session(d)
            except Exception as exc:
                log.exception("session %s %s raised", self.symbol, d)
                result = SessionResult(
                    session_date=d, realised_pnl=0.0, unrealised_pnl=0.0,
                    fills_count=0, commission_paid=0.0, error=str(exc),
                )
            sessions.append(result)

        return _aggregate(self.strategy_factory().strategy_id, self.symbol, sessions)

    async def _run_one_session(self, session_date: date) -> SessionResult:
        # Each session: brand-new strategy, brand-new engine, brand-new
        # ledger. The cache layer behind the bus makes the per-day
        # yfinance fetch a no-op after the first run.
        strategy = self.strategy_factory()
        as_datetime = datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc)
        bus, router = build_session(
            broker=self.broker,
            symbols=[self.symbol],
            session_date=as_datetime,
            interval=self.interval,
            slippage_bps=self.slippage_bps,
            pace_seconds=self.pace_seconds,
        )
        engine = Engine(bus=bus, router=router)
        engine.register_strategy(strategy, symbols=[self.symbol], capital_usd=self.capital_usd)
        snapshot = await engine.run(as_datetime)
        book = _find_book(snapshot, strategy.strategy_id)
        if book is None:
            return SessionResult(
                session_date=session_date, realised_pnl=0.0, unrealised_pnl=0.0,
                fills_count=0, commission_paid=0.0, snapshot=snapshot,
            )
        return SessionResult(
            session_date=session_date,
            realised_pnl=book["realised_pnl"],
            unrealised_pnl=book["unrealised_pnl"],
            fills_count=book["fills_count"],
            commission_paid=book["commission_paid"],
            snapshot=snapshot,
        )


# ---- Internal helpers ----------------------------------------------

def _business_days(start: date, end: date) -> Iterable[date]:
    """Mon–Fri between start and end inclusive. Skipping US holidays
    relies on yfinance returning empty bars — strategies see no bars,
    emit no orders, session yields realised_pnl=0. Operator can filter
    those out post-hoc if needed."""
    d = start
    one = timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += one


def _find_book(snapshot: dict, strategy_id: str) -> dict | None:
    for book in snapshot.get("strategies", []):
        if book.get("strategy_id") == strategy_id:
            return book
    return None


def _aggregate(
    strategy_id: str, symbol: str, sessions: list[SessionResult]
) -> WalkForwardResult:
    realised = [s.realised_pnl for s in sessions if s.error is None]
    total = sum(realised)
    n = len(realised)
    avg = total / n if n else 0.0
    stdev = _stdev(realised, avg) if n > 1 else 0.0
    sharpe = (avg / stdev) if stdev > 0 else 0.0
    wins = sum(1 for r in realised if r > 0)
    win_pct = (wins / n) if n else 0.0

    # Equity curve = running sum of realised P&L. Max drawdown = max
    # (running_peak - current) across the curve. Standard backtest stats.
    equity_curve: list[tuple[date, float]] = []
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for s in sessions:
        if s.error is not None:
            continue
        running += s.realised_pnl
        equity_curve.append((s.session_date, running))
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    completed = [s for s in sessions if s.error is None]
    best = max(completed, key=lambda s: s.realised_pnl) if completed else None
    worst = min(completed, key=lambda s: s.realised_pnl) if completed else None

    return WalkForwardResult(
        strategy_id=strategy_id,
        symbol=symbol,
        sessions=sessions,
        total_realised_pnl=total,
        total_fills=sum(s.fills_count for s in sessions),
        total_commission=sum(s.commission_paid for s in sessions),
        avg_session_pnl=avg,
        stdev_session_pnl=stdev,
        win_session_pct=win_pct,
        sharpe_per_session=sharpe,
        max_drawdown=max_dd,
        best_session=best,
        worst_session=worst,
        equity_curve=equity_curve,
    )


def _stdev(values: list[float], mean: float) -> float:
    """Sample standard deviation. Plain Python — keeping numpy out of
    this module so it loads in environments where numpy isn't required."""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


__all__ = [
    "WalkForwardValidator",
    "WalkForwardResult",
    "SessionResult",
    "StrategyFactory",
]
