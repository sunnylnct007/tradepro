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
from ...secrets import get_secret


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

    # Env-overridable so the daemon can target our paper Gateway on 7500
    # (Docker holds 4001/4002; TWS uses 7496/7497 — 7500 keeps us clear).
    host: str = field(default_factory=lambda: os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("TRADEPRO_IBKR_PORT", "7500")))
    # Unique per concurrent connection (so we don't clash with the live
    # data-harvesting session's client id).
    client_id: int = field(default_factory=lambda: int(os.environ.get("TRADEPRO_IBKR_CLIENT_ID", "17")))
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


# G10 currency codes — used to detect an FX pair symbol so the router/bus
# build a Forex contract instead of a Stock. "EURUSD" / "EUR/USD" → Forex;
# anything else falls through to a US equity Stock on SMART/USD.
_FX_CCYS = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "SEK", "NOK"}


def _ib_contract(ib_insync, symbol: str):
    """Resolve a symbol to the right ib_insync contract: Forex for a G10 pair
    (so the FX desk can run on IBKR), Stock for everything else."""
    s = symbol.replace("/", "").upper()
    if len(s) == 6 and s[:3] in _FX_CCYS and s[3:] in _FX_CCYS:
        return ib_insync.Forex(s)
    return ib_insync.Stock(symbol, "SMART", "USD")


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
            s: _ib_contract(ib_insync, s) for s in self.symbols
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
                # Yield to the engine loop (ib_insync is on it) so its real-time
                # bar events process. (Was asyncio.to_thread(ib.sleep,…) → ran in
                # a loopless worker thread → "no current event loop" crash.)
                await asyncio.sleep(0.1)
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
    # PHASE 2: place through the central gateway's ONE connection (write an
    # intent to its inbox, read the fill from its outbox) instead of opening
    # our OWN ib_insync connection — which contended with the gateway over the
    # single account's budget and caused the morning connect-timeouts. Default
    # ON; set TRADEPRO_IBKR_ORDERS_VIA_GATEWAY=0 to fall back to direct placement.
    route_via_gateway: bool = field(
        default_factory=lambda: os.environ.get("TRADEPRO_IBKR_ORDERS_VIA_GATEWAY", "1") != "0")

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
        # PHASE 2 default: never open our own connection — submit intents to the
        # central gateway and let its ONE connection place them. Eliminates the
        # per-desk connection contention entirely.
        if self.route_via_gateway:
            await self._run_via_gateway(approved_queue, bar_queue, fill_queue, shutdown_queue)
            return

        ib_insync = _try_import_ib_insync()
        if ib_insync is None:
            log.error("ib_insync not installed — IBKRRouter cannot run")
            await fill_queue.put(ShutdownEvent(reason="ib_insync missing"))
            return
        ib = await self.connection.connect()

        # Idempotency guard: this strategy is SWING but the daemon reruns every
        # ~15 min, and a placed OPG/MOO order rests until the auction. Without
        # this, each rerun re-seeds the still-unfilled positions, re-emits the
        # same entry/exit, and stacks DUPLICATE orders at IBKR (placeOrder fires
        # before the OMS dedup). Snapshot the broker's existing open orders once
        # at connect, keyed by (symbol, action); _handle_approval skips any
        # order that already has a live broker order. The broker is the golden
        # source — this is the authoritative "do I already have this working?".
        self._open_keys: set[tuple[str, str]] = set()
        try:
            for tr in await ib.reqAllOpenOrdersAsync():
                self._open_keys.add((tr.contract.symbol, tr.order.action))
            log.info("IBKR open-order snapshot: %d existing (dedup guard armed)",
                     len(self._open_keys))
        except Exception as exc:  # noqa: BLE001
            log.warning("IBKR open-order snapshot failed (%s) — dedup guard off this run", exc)

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

    async def _run_via_gateway(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        """Gateway-routed run loop: NO own IBKR connection. Each approval is
        submitted to the central gateway's order inbox; the gateway's single
        connection places it and writes the fill back to the outbox, which we
        poll to emit the FillEvent."""
        log.info(
            "IBKRRouter '%s': routing orders via the CENTRAL gateway inbox "
            "(no own IBKR connection — contention-free)", self.name)
        bar_drain = asyncio.create_task(self._drain_bars(bar_queue))
        try:
            while True:
                msg = await approved_queue.get()
                if isinstance(msg, ShutdownEvent):
                    await fill_queue.put(ShutdownEvent(reason="ibkr shutdown"))
                    return
                assert isinstance(msg, OrderApproved)
                await self._handle_via_gateway(msg, fill_queue)
        finally:
            if not bar_drain.done():
                bar_drain.cancel()

    async def _handle_via_gateway(
        self, approval: OrderApproved, fill_queue: asyncio.Queue
    ) -> None:
        order = approval.order
        if order.type != OrderType.MARKET:
            log.warning(
                "IBKRRouter only supports MARKET orders today; got %s for %s",
                order.type.value, order.symbol)
            return
        account = self.accounts_by_strategy_id.get(order.strategy_id, self.default_account)
        if not account:
            log.error("No IBKR account mapped for strategy_id=%s and no default set",
                      order.strategy_id)
            return
        if not self._live_orders_enabled(account):
            log.info("IBKR WOULD-PLACE (gateway) · account=%s sid=%s %s %s qty=%s",
                     account, order.strategy_id, order.side.value, order.symbol, order.quantity)
            return

        import hashlib
        import uuid as _uuid
        from ...ibkr_gateway import read_order_result, submit_order_intent

        # Deterministic intent_id from (strategy, symbol, side, qty, bar_ts) so a
        # 15-min rerun of the same approval can't double-place — the gateway
        # skips an intent whose result already exists.
        bar_ts = getattr(getattr(approval, "bar_at_approval", None), "timestamp", None)
        seed = (f"{order.strategy_id}:{order.symbol}:{order.side.value}:"
                f"{int(order.quantity)}:{bar_ts.isoformat() if bar_ts else ''}")
        iid = str(_uuid.UUID(hashlib.md5(seed.encode()).hexdigest()))
        action = "BUY" if order.side == OrderSide.BUY else "SELL"
        submit_order_intent({
            "intent_id": iid,
            "account": account,
            "symbol": order.symbol,
            "action": action,
            "quantity": float(order.quantity),
            "order_type": "MKT",
            "strategy_id": order.strategy_id,
        })
        log.info("IBKR→gateway · sid=%s %s %s qty=%s intent=%s",
                 order.strategy_id, action, order.symbol, order.quantity, iid[:8])
        # NOTE: OMS recording is owned by the GATEWAY now (record_to_oms), not
        # the desk — the gateway is the one place with both the fill AND the
        # strategy_id, and the desk's mirror was unreliable (FX clone showed 0
        # OMS orders despite filling). We just emit the FillEvent for the local
        # engine ledger below.

        # Poll the outbox for the gateway's result (~12s). OPG/dup return fast;
        # an RTH market fills within the gateway's 8s wait. If nothing yet, the
        # gateway still places it and the fill reconciles from the book.
        result = None
        for _ in range(24):
            result = read_order_result(iid)
            if result is not None:
                break
            await asyncio.sleep(0.5)
        if result is None:
            log.info("IBKR→gateway · %s %s: no result yet — gateway will place; "
                     "fill reconciles from the book", action, order.symbol)
            return
        status = (result.get("status") or "").lower()
        if status == "filled" and float(result.get("fill_qty") or 0) > 0:
            fqty = float(result["fill_qty"])
            fprice = float(result.get("fill_price") or 0.0)
            boid = str(result.get("broker_order_id") or iid)
            await fill_queue.put(FillEvent(fill=Fill(
                order_id=boid,
                strategy_id=order.strategy_id,
                symbol=order.symbol,
                side=order.side,
                quantity=int(fqty),
                fill_price=fprice,
                fill_time=datetime.now(timezone.utc),
                commission=0.0,
            )))
            log.info("IBKR→gateway FILLED · %s %s qty=%s @ %s",
                     action, order.symbol, result.get("fill_qty"), result.get("fill_price"))
        else:
            log.info("IBKR→gateway · %s %s -> %s (no fill emitted; reconciles from the book)",
                     action, order.symbol, status)

    async def _record_in_oms(self, order, ib_order) -> None:
        """Mirror an already-placed IBKR paper order into the OMS for VISIBILITY.

        IBKR is placed by THIS Python router (ib_insync); the .NET IBKR placement
        is kill-switched off (IBKR:AllowOrders=false), so posting here records the
        order SUBMITTED and the backend NEVER re-places it — the OMS is a mirror,
        not the placer. Idempotent via a deterministic ClientOrderId. Best-effort:
        any failure is swallowed so it can't disturb the real placement.
        """
        try:
            import hashlib
            import uuid as _uuid
            from datetime import datetime as _dt, timezone as _tz
            try:
                import httpx
            except ImportError:
                return
            api_base = get_secret("api-base-url")
            api_token = get_secret("api-token")
            if not api_base or not api_token:
                return
            # Deterministic id from (strategy, symbol, side, qty, session date) so
            # a 15-min rerun that re-records the same MOO order dedups (OMS unique
            # index → 409) instead of duplicating.
            day = _dt.now(_tz.utc).date().isoformat()
            seed = (f"{order.strategy_id}:{order.symbol}:{order.side.value}:"
                    f"{int(order.quantity)}:{day}")
            client_id = str(_uuid.UUID(hashlib.md5(seed.encode()).hexdigest()))
            intent = {
                "ClientOrderId": client_id,
                "Broker": "IBKR_PAPER",
                "Symbol": order.symbol,
                "Side": order.side.value,
                "Qty": float(order.quantity),
                "OrderType": "MKT",
                "StrategyId": order.strategy_id,
                "PlacedBy": "STRATEGY_AUTO",
                "TimeInForce": getattr(ib_order, "tif", "DAY") or "DAY",
            }
            url = f"{api_base.rstrip('/')}/api/oms/orders"
            headers = {"Authorization": f"Bearer {api_token}"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=intent, headers=headers)
                if resp.status_code == 409:
                    return None  # already recorded this session — idempotent
                if resp.status_code not in (200, 201):
                    log.warning("OMS record (IBKR) %s for %s: %s",
                                resp.status_code, order.symbol, resp.text[:160])
                    return None
                oid = (resp.json() or {}).get("id")
                # Approve → ApproveAsync hits the IBKR kill-switch and leaves the
                # order SUBMITTED WITHOUT placing (safe mirror of our OPG queue).
                if oid:
                    await client.post(f"{url}/{oid}/approve", json={}, headers=headers)
                return oid  # caller marks it FILLED once the gateway reports the fill
        except Exception:
            log.exception(
                "OMS record (IBKR) failed for %s — not on /oms (placement unaffected)",
                getattr(order, "symbol", "?"))
        return None

    async def _mark_oms_filled(self, oid, qty: float, price: float, broker_fill_id: str) -> None:
        """Mark the OMS mirror order FILLED with the gateway's fill, so the /oms
        + cockpit show FILLED (not a stuck SUBMITTED). Gateway-routed IBKR orders
        place on the gateway's connection, so the OMS never learns the fill on
        its own — we report it here. Best-effort; never disturbs trading."""
        if not oid or qty <= 0 or price <= 0:
            return
        try:
            import httpx
            from ...secrets import get_secret
            api_base = get_secret("api-base-url")
            api_token = get_secret("api-token")
            if not api_base or not api_token:
                return
            url = f"{api_base.rstrip('/')}/api/oms/orders/{oid}/fill"
            body = {"Qty": float(qty), "Price": float(price), "Fee": 0.0,
                    "Currency": "USD", "BrokerFillId": str(broker_fill_id or "")}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url, json=body, headers={"Authorization": f"Bearer {api_token}"})
                if resp.status_code not in (200, 201, 409):
                    log.warning("OMS fill-mark %s for oid=%s: %s",
                                resp.status_code, oid, resp.text[:160])
        except Exception:
            log.exception("OMS fill-mark failed for oid=%s (placement unaffected)", oid)

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

        contract = _ib_contract(ib_insync, order.symbol)  # Stock or Forex
        ib_action = "BUY" if order.side == OrderSide.BUY else "SELL"

        # Idempotency: if a live broker order for this (symbol, action) already
        # exists (placed by an earlier rerun and still resting in the auction),
        # do NOT stack a duplicate. The broker snapshot is the golden source.
        key = (order.symbol, ib_action)
        if key in getattr(self, "_open_keys", set()):
            log.info(
                "IBKR skip-duplicate · %s %s qty=%s — a live broker order already "
                "exists (rerun before the prior order filled)",
                ib_action, order.symbol, order.quantity,
            )
            return

        ib_order = ib_insync.MarketOrder(ib_action, order.quantity)
        ib_order.account = account
        # MOO placement: if the US equity venue is CLOSED (pre-market), submit as
        # an opening-auction order (TIF=OPG) so it queues for the open instead of
        # a plain DAY market that the Gateway flaps via its preset. During RTH,
        # leave the default (immediate market). This is the native-MOO placement
        # that supports_moo("ibkr")=True promises. (If a Gateway order preset
        # forces TIF=DAY it still goes PreSubmitted and fills at the open.)
        try:
            from datetime import datetime as _dt, timezone as _tz
            from .. import market_hours
            from ...bar_cache.asset_class_resolver import resolve_asset_class
            if not market_hours.is_open(resolve_asset_class(order.symbol), _dt.now(_tz.utc)):
                # OPG (market-on-open) is an EQUITY/auction concept. Forex
                # (secType CASH on IDEALPRO) has NO opening auction, so IBKR
                # rejects OPG outright (Error 201: "time-in-force OPG is
                # invalid for this combination") — which silently killed every
                # FX-clone order. Only queue equities/ETFs (STK) as OPG; forex
                # places a normal market order (24/5, no auction to wait for).
                if getattr(contract, "secType", "") == "STK":
                    ib_order.tif = "OPG"
        except Exception:
            pass
        trade = ib.placeOrder(contract, ib_order)
        # Remember it so a later order in THIS run can't double it either.
        try:
            self._open_keys.add(key)
        except Exception:  # noqa: BLE001
            pass

        # Mirror into the OMS for VISIBILITY (record-only — the .NET IBKR
        # placement is kill-switched off via IBKR:AllowOrders=false, so the OMS
        # records it SUBMITTED and NEVER re-places). Best-effort: never disturbs
        # the real ib_insync placement above.
        await self._record_in_oms(order, ib_order)

        # An OPG (market-on-open) order can only fill in the auction — there's
        # NOTHING to wait for, so record + return immediately. (Waiting 8s/order
        # made a 50-name session take minutes and the snapshot never posted.)
        if getattr(ib_order, "tif", "") == "OPG":
            await asyncio.sleep(0.2)  # let it reach PendingSubmit/PreSubmitted
            log.info(
                "IBKR OPG order QUEUED · %s %s qty=%s status=%s — fills at the "
                "opening auction (reconcile from the IBKR book).",
                order.side.value, order.symbol, order.quantity,
                trade.orderStatus.status,
            )
            return
        # RTH market order — fills promptly. Bounded wait (ib_insync is on the
        # engine loop, so yielding processes its socket events); if still working,
        # record + return rather than block the engine.
        waited = 0.0
        while not trade.isDone() and waited < 8.0:
            await asyncio.sleep(0.5)
            waited += 0.5
        if not trade.isDone():
            log.info(
                "IBKR order QUEUED · %s %s qty=%s status=%s — not blocking "
                "(reconcile fill from the IBKR book).",
                order.side.value, order.symbol, order.quantity,
                trade.orderStatus.status,
            )
            return

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
