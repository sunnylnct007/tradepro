"""Interactive Brokers integration via ib_insync — bars + orders.

What IBKR gives us that T212 doesn't:
  - Real-time intraday bars (delayed or live depending on subscription)
  - Limit / stop / bracket orders + working-order management
  - Sub-account routing — one IBKR umbrella, many sub-accounts, one
    per strategy. The paper engine's `strategy_id` lines up 1:1 with
    sub-account codes.
  - Paper-account mode (IBKR's "DU" prefixed accounts) — same wire
    protocol as live, just a sandboxed money pool. Use these for
    realistic dry-runs without writing a simulator.

Why this file is a SKELETON today:
  - `ib_insync` isn't a hard dependency yet; importing it lazily so
    the rest of the engine runs on machines without IBKR installed.
  - The Client Portal Gateway / TWS launch ritual is operator-driven;
    automating it lives in a follow-up doc. The skeleton refuses to
    pretend it's connected when no gateway is reachable.
  - Sub-account routing needs the `account` parameter wired into
    every order — the placeholder is here but the routing table
    (strategy_id → account_id) is the operator's config knob.

Safety story: live IBKR orders refuse to send unless the account id
starts with "DU" (paper) OR `allow_real_orders=True` is set AND the
env var `TRADEPRO_IBKR_ALLOW_LIVE=1`. Same two-key posture as T212.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..bar_bus import BarBus
from ..messages import (
    BarEvent,
    FillEvent,
    OrderApproved,
    ShutdownEvent,
)
from ..router import OrderRouter
from ..strategy import Bar, Fill, OrderSide, OrderType


log = logging.getLogger("tradepro.paper.ibkr")


def _try_import_ib_insync():
    """Lazy import. Returns the module or None — keeps the engine
    importable on machines without ib_insync installed (the rest of
    the paper package doesn't need it)."""
    try:
        import ib_insync  # type: ignore
        return ib_insync
    except ImportError:
        return None


@dataclass
class IBKRConnection:
    """Holds the shared ib_insync.IB instance so the bar bus and the
    order router don't open two gateway sockets to the same TWS / Client
    Portal Gateway. Construct once, pass to both."""

    host: str = "127.0.0.1"
    port: int = 7497          # TWS paper port; 7496 live, 4001/4002 for IB Gateway
    client_id: int = 17       # arbitrary; must be unique per concurrent connection
    timeout_seconds: float = 10.0
    _ib: object | None = None  # ib_insync.IB instance

    async def connect(self) -> object:
        ib_insync = _try_import_ib_insync()
        if ib_insync is None:
            raise RuntimeError(
                "ib_insync is not installed. Add it to the project "
                "(`uv add ib_insync`) before using the IBKR adapter."
            )
        if self._ib is None:
            self._ib = ib_insync.IB()
        if not self._ib.isConnected():
            await self._ib.connectAsync(
                self.host, self.port,
                clientId=self.client_id,
                timeout=self.timeout_seconds,
            )
        return self._ib

    async def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()


@dataclass
class IBKRBarBus(BarBus):
    """Real-time bar feed from IBKR.

    Each bar comes off `ib.reqRealTimeBars` (5s native) and gets
    aggregated to `timeframe_seconds` by the bus. The aggregation
    keeps OHLC across the window: open from the first slice, high/low
    rolling max/min, close from the last, volume summed.

    `contracts` is a dict of `symbol → ib_insync.Contract`; build it
    once per session. Today we instantiate `Stock(SYMBOL, "SMART",
    "USD")` for US large caps; non-US venues need explicit Contract
    construction the operator supplies.
    """

    symbols: list[str] = field(default_factory=list)
    connection: IBKRConnection = field(default_factory=IBKRConnection)
    timeframe_seconds: int = 60
    name: str = "ibkr_bus"
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if not self.symbols:
            await out_queue.put(ShutdownEvent(reason="ibkr_bus: no symbols"))
            return
        ib_insync = _try_import_ib_insync()
        if ib_insync is None:
            log.error("ib_insync not installed — IBKRBarBus cannot run")
            await out_queue.put(ShutdownEvent(reason="ib_insync missing"))
            return

        ib = await self.connection.connect()
        # SMART/USD covers US equities; equivalents for forex/futures
        # are the operator's job (Forex/Future/Contract subclasses).
        contracts = {
            s: ib_insync.Stock(s, "SMART", "USD") for s in self.symbols
        }
        sequence = 0
        # ib_insync exposes real-time bars as a RealTimeBarList object
        # whose `updateEvent` fires for each new 5s slice.
        bar_lists = {
            sym: ib.reqRealTimeBars(c, 5, "TRADES", useRTH=False)
            for sym, c in contracts.items()
        }
        # Aggregator state per symbol — collapses N slices into one
        # timeframe_seconds bar before publishing.
        agg: dict[str, dict] = {s: {} for s in self.symbols}

        watcher = asyncio.create_task(self._shutdown_watcher(shutdown_queue))
        try:
            while not self._stop.is_set():
                # Drain ib_insync's event loop a slice at a time. Run
                # ib.sleep(0.1) under to_thread so we don't block the
                # async loop the engine is on.
                await asyncio.to_thread(ib.sleep, 0.1)
                for sym, rtbars in bar_lists.items():
                    if not rtbars:
                        continue
                    last = rtbars[-1]
                    a = agg[sym]
                    if not a:
                        a.update(open=last.open_, high=last.high, low=last.low,
                                 close=last.close, volume=last.volume,
                                 started=last.time)
                    else:
                        a["high"] = max(a["high"], last.high)
                        a["low"] = min(a["low"], last.low)
                        a["close"] = last.close
                        a["volume"] += last.volume
                    elapsed = (last.time - a["started"]).total_seconds()
                    if elapsed + 0.001 >= self.timeframe_seconds:
                        bar = Bar(
                            symbol=sym,
                            timestamp=a["started"].astimezone(timezone.utc),
                            open=float(a["open"]),
                            high=float(a["high"]),
                            low=float(a["low"]),
                            close=float(a["close"]),
                            volume=int(a["volume"]),
                            timeframe_seconds=self.timeframe_seconds,
                        )
                        await out_queue.put(BarEvent(bar=bar, sequence=sequence))
                        sequence += 1
                        agg[sym] = {}
        finally:
            watcher.cancel()
            for rtbars in bar_lists.values():
                ib.cancelRealTimeBars(rtbars)
            await self.connection.disconnect()
            await out_queue.put(ShutdownEvent(reason="ibkr_bus exhausted"))

    async def _shutdown_watcher(self, shutdown_queue: asyncio.Queue) -> None:
        msg = await shutdown_queue.get()
        if isinstance(msg, ShutdownEvent):
            self._stop.set()


@dataclass
class IBKRRouter(OrderRouter):
    """Real-broker router for Interactive Brokers.

    Uses ib_insync's `placeOrder(contract, order)` which returns a
    `Trade` object whose `fillEvent` fires for each partial fill. We
    coalesce partials into one Fill per terminal trade — same shape
    the rest of the paper engine emits.

    Sub-account routing: each Order carries the strategy's id; the
    router's `accounts_by_strategy_id` map turns that into the IBKR
    account code (e.g. "DU1234567" for paper, "U1234567" for live).
    Missing entries → fall back to `default_account`; missing default
    → loud rejection.
    """

    connection: IBKRConnection = field(default_factory=IBKRConnection)
    default_account: Optional[str] = None
    accounts_by_strategy_id: dict[str, str] = field(default_factory=dict)
    allow_real_orders: bool = False
    name: str = "ibkr_router"

    def __post_init__(self) -> None:
        if self.default_account is None:
            self.default_account = os.environ.get("TRADEPRO_IBKR_ACCOUNT")

    def _live_orders_enabled(self, account: str) -> bool:
        """Paper accounts (DU prefix) are always allowed. Live accounts
        need both the constructor flag and the env override."""
        if account and account.startswith("DU"):
            return True
        return (
            self.allow_real_orders
            and os.environ.get("TRADEPRO_IBKR_ALLOW_LIVE") == "1"
        )

    async def run(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        ib_insync = _try_import_ib_insync()
        if ib_insync is None:
            log.error("ib_insync not installed — IBKRRouter cannot run")
            await fill_queue.put(ShutdownEvent(reason="ib_insync missing"))
            return
        ib = await self.connection.connect()

        bar_drain = asyncio.create_task(self._drain_bars(bar_queue))
        try:
            while True:
                msg = await approved_queue.get()
                if isinstance(msg, ShutdownEvent):
                    await fill_queue.put(ShutdownEvent(reason="ibkr shutdown"))
                    return
                assert isinstance(msg, OrderApproved)
                await self._handle_approval(ib, ib_insync, msg, fill_queue)
        finally:
            if not bar_drain.done():
                bar_drain.cancel()
            await self.connection.disconnect()

    async def _drain_bars(self, bar_queue: asyncio.Queue) -> None:
        while True:
            msg = await bar_queue.get()
            if isinstance(msg, ShutdownEvent):
                return

    async def _handle_approval(
        self,
        ib,
        ib_insync,
        approval: OrderApproved,
        fill_queue: asyncio.Queue,
    ) -> None:
        order = approval.order
        if order.type != OrderType.MARKET:
            log.warning(
                "IBKRRouter only supports MARKET orders today; got %s for %s",
                order.type.value, order.symbol,
            )
            return
        account = self.accounts_by_strategy_id.get(
            order.strategy_id, self.default_account
        )
        if not account:
            log.error(
                "No IBKR account mapped for strategy_id=%s and no default set",
                order.strategy_id,
            )
            return
        if not self._live_orders_enabled(account):
            log.info(
                "IBKR WOULD-PLACE · account=%s · sid=%s · %s %s qty=%s tag=%s",
                account, order.strategy_id, order.side.value, order.symbol,
                order.quantity, order.tag,
            )
            return

        contract = ib_insync.Stock(order.symbol, "SMART", "USD")
        ib_action = "BUY" if order.side == OrderSide.BUY else "SELL"
        ib_order = ib_insync.MarketOrder(ib_action, order.quantity)
        ib_order.account = account
        trade = ib.placeOrder(contract, ib_order)

        # Wait for the trade to reach a terminal status, then coalesce
        # all fills into a single Fill event for the rest of the engine.
        # ib_insync exposes `trade.isDone()` and `trade.fills`.
        while not trade.isDone():
            await asyncio.to_thread(ib.sleep, 0.5)

        if not trade.fills:
            log.warning(
                "IBKR trade for %s terminated without fills (status=%s)",
                order.symbol, trade.orderStatus.status,
            )
            return
        total_qty = sum(int(f.execution.shares) for f in trade.fills)
        total_value = sum(
            float(f.execution.shares) * float(f.execution.price)
            for f in trade.fills
        )
        avg_price = total_value / max(1, total_qty)
        commission = sum(
            float(getattr(f.commissionReport, "commission", 0.0) or 0.0)
            for f in trade.fills
        )
        fill = Fill(
            order_id=str(trade.order.orderId),
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=total_qty,
            fill_price=avg_price,
            fill_time=datetime.now(timezone.utc),
            commission=commission,
        )
        await fill_queue.put(FillEvent(fill=fill))


__all__ = ["IBKRBarBus", "IBKRRouter", "IBKRConnection"]
