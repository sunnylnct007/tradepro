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

What's here now:
  - `Strategy`, `RiskLimits`           strategy abstractions + risk gate
  - `Bar`, `Order`, `Fill`, `Position` wire-level dataclasses
  - `BarBus`                           ReplayBarBus + YfinanceIntradayBus
  - `OrderRouter`                      PaperOrderRouter + StubLiveRouter
  - `brokers.T212OrderRouter`          Trading 212 REST execution
  - `brokers.IBKRBarBus`,
    `brokers.IBKRRouter`               Interactive Brokers via ib_insync
  - `RiskService`, `Ledger`            queue-driven risk + P&L attribution
  - `Engine`                           wires bus → strategy → risk → router → ledger
  - `profiles.build_session(broker=)`  one-call factory across all of the above

Still pending:
  - Working-orders queue for LIMIT / STOP orders
  - Multi-symbol Yfinance multiplex bus
  - Validator — walk-forward + regime-conditioned backtest gate
"""
from .strategy import Bar, Fill, Order, OrderSide, OrderType, Position, Strategy
from .risk import RiskLimits, RiskCheckResult
from .bar_bus import BarBus, ReplayBarBus, YfinanceIntradayBus, static_bars
from .router import OrderRouter, PaperOrderRouter, StubLiveRouter
from .multi_router import MultiBrokerRouter
from .ledger import Ledger
from .risk_service import RiskService
from .engine import Engine

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
    "BarBus",
    "ReplayBarBus",
    "YfinanceIntradayBus",
    "static_bars",
    "OrderRouter",
    "PaperOrderRouter",
    "StubLiveRouter",
    "MultiBrokerRouter",
    "Ledger",
    "RiskService",
    "Engine",
]
