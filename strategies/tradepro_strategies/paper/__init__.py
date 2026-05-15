"""Intraday paper-trading scaffolding.

Parallel engine to the daily-timeframe comparator. Lives alongside
the existing strategies/ folder rather than merging into it because
the time scale (minute bars vs. daily), the lifecycle (per-session
open/close vs. continuous), and the broker integration (IBKR vs.
none) all diverge enough that a shared abstraction would have to
fork on every method.

What's here:
  - `Strategy`              event-driven base class for intraday rules
  - `RiskLimits`            position-size and loss caps + halt logic
  - `Bar`, `Order`, `Fill`,
    `Position`              wire-level dataclasses the engine passes
                            between bus / strategies / order router
  - `strategies/`           concrete strategy implementations,
                            starting with OpeningRangeBreakout

Not here yet (next milestones in the roadmap):
  - BarBus        — live IBKR streaming bars + replay-from-csv mode
  - OrderRouter   — IBKR Client Portal Gateway integration with
                    sub-account routing by strategy_id
  - Ledger        — per-strategy P&L attribution and reconciliation
  - Validator     — walk-forward + regime-conditioned backtest gate
"""
from .strategy import Bar, Fill, Order, OrderSide, OrderType, Position, Strategy
from .risk import RiskLimits, RiskCheckResult

__all__ = [
    "Bar",
    "Fill",
    "Order",
    "OrderSide",
    "OrderType",
    "Position",
    "Strategy",
    "RiskLimits",
    "RiskCheckResult",
]
