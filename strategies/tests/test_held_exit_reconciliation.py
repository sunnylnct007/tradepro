"""Held-exit reconciliation — the fix for held positions that never exit because
the live feed delivered no trigger bar (root cause 2026-07-04: thin names like
STRL/STX/WDC get no intraday bar → on_bar never runs → the signal-EXIT is never
sold, and the position bleeds forever).

The fix has two parts, both pinned here:
  1. HeldReconciliationBus (bar_bus) — after the inner bus exhausts, emits one
     synthetic bar per held symbol, THEN the shutdown. Guarantees delivery.
  2. on_bar already exits a held long on a flat signal — so a synthetic bar is
     enough to trigger the exit. These tests prove that with the SAME harness the
     risk-control parity tests use, AND that entries stay unchanged (parity) and a
     held name can't double-exit (the _moo_fired dedup).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from tradepro_strategies.paper.bar_bus import HeldReconciliationBus
from tradepro_strategies.paper.messages import BarEvent, ShutdownEvent
from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy
from tradepro_strategies.paper.strategy import Bar, OrderSide


# ── harness (mirrors test_equity_risk_controls) ─────────────────────────────
def _uptrend_df(n: int = 300, start: float = 100.0, end: float = 250.0) -> pd.DataFrame:
    close = np.linspace(start, end, n)
    return pd.DataFrame({"High": close * 1.01, "Low": close * 0.99, "Close": close})


def _flat_signal_df(n: int = 300, level: float = 100.0) -> pd.DataFrame:
    close = np.linspace(level * 1.5, level, n)  # steady decline → signal 0 (exit)
    return pd.DataFrame({"High": close * 1.01, "Low": close * 0.99, "Close": close})


def _stale_ts() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=30)


def _bar(symbol: str, close: float, is_live: bool = False) -> Bar:
    return Bar(symbol=symbol, timestamp=_stale_ts(), open=close, high=close * 1.01,
               low=close * 0.99, close=close, volume=1_000_000,
               timeframe_seconds=86400, is_live=is_live)


def _make_strategy(symbols, data_map, **extra):
    params = {"symbols": list(symbols), "use_regime_filter": False,
              "_data_fn": lambda s: data_map.get(s), "capital_usd": 100_000.0,
              # Sustained-uptrend fixtures exercise reconciliation, not the fresh
              # gate — turn it off so the sustained long still enters (the fresh
              # gate has its own tests).
              "sleeve_size": 20, "entry_fresh_only": False, **extra}
    strat = IchimokuEquityStrategy(strategy_id="test-held-exit", params=params)
    strat.on_session_start(_stale_ts())
    return strat


def _seed(strat, sym, qty):
    strat.seed_positions({sym: qty})
    strat._positions[sym] = qty


# ── (1) the fix: a held flat-signal name exits on a (synthetic) bar ──────────
def test_held_flat_signal_exits_on_synthetic_bar():
    """The core case: STRL is held, its signal has flipped to FLAT, and the only
    bar it gets is the synthetic reconciliation bar → it must SELL to flatten."""
    strat = _make_strategy(["STRL"], {"STRL": _flat_signal_df()})
    _seed(strat, "STRL", 1)
    orders = strat.on_bar(_bar("STRL", 700.0, is_live=True))
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL
    assert orders[0].quantity == 1  # flatten exactly — never oversell/short


# ── (2) parity: a held LONG name is a no-op (no spurious exit) ───────────────
def test_held_long_signal_no_exit():
    strat = _make_strategy(["STRL"], {"STRL": _uptrend_df()})
    _seed(strat, "STRL", 1)
    assert strat.on_bar(_bar("STRL", 250.0, is_live=True)) == []


# ── (3) parity: entries are unchanged (a flat name with a long signal buys) ──
def test_flat_name_entry_unchanged():
    strat = _make_strategy(["AAPL"], {"AAPL": _uptrend_df()})
    orders = strat.on_bar(_bar("AAPL", 250.0, is_live=True))
    assert len(orders) == 1 and orders[0].side == OrderSide.BUY and orders[0].quantity > 0


# ── (4) no double-exit: _moo_fired dedups a 2nd bar for the same symbol ──────
def test_no_double_exit_same_session():
    strat = _make_strategy(["STRL"], {"STRL": _flat_signal_df()})
    _seed(strat, "STRL", 1)
    first = strat.on_bar(_bar("STRL", 700.0))
    second = strat.on_bar(_bar("STRL", 700.0))  # a real bar AND the synthetic one
    assert len(first) == 1 and first[0].side == OrderSide.SELL
    assert second == []  # dedup → no second sell / oversell


# ── (5) the bus: forwards inner bars, then appends synthetic, then shutdown ──
class _FakeInnerBus:
    """Minimal duck-typed inner bus: emit given bars + a ShutdownEvent."""
    name = "fake_inner"

    def __init__(self, bars):
        self._bars = bars

    async def run(self, out_queue, shutdown_queue):
        for i, b in enumerate(self._bars):
            await out_queue.put(BarEvent(bar=b, sequence=i))
        await out_queue.put(ShutdownEvent(reason="fake exhausted"))


def test_reconciliation_bus_appends_synthetic_then_shutdown():
    real = [_bar("AAPL", 100.0)]
    synth = [_bar("STRL", 700.0), _bar("STX", 800.0)]
    bus = HeldReconciliationBus(inner=_FakeInnerBus(real), reconciliation_bars=synth)
    out: asyncio.Queue = asyncio.Queue()
    asyncio.run(bus.run(out, asyncio.Queue()))
    events = []
    while not out.empty():
        events.append(out.get_nowait())
    bar_syms = [e.bar.symbol for e in events if isinstance(e, BarEvent)]
    assert bar_syms == ["AAPL", "STRL", "STX"]  # real FIRST, then reconciliation
    assert isinstance(events[-1], ShutdownEvent)  # exactly one terminal shutdown
    assert sum(isinstance(e, ShutdownEvent) for e in events) == 1
