"""T212OrderRouter — places equity orders against Trading 212's REST API.

Why this exists: the paper engine should be able to route approved
orders to a real (or T212-demo) account, not just simulate fills.
T212 is the obvious first stop because the operator already has a
T212 account wired into the .NET backend for portfolio reads.

What T212 does NOT give us:
  - OHLC / quotes / bars  (see memory: T212 has portfolio + instruments
    + orders, but NO market data — yfinance still drives the BarBus
    when T212 is the router).
  - Granular fill events. T212 returns the order resource with its
    current status — `FILLED`, `WORKING`, etc. We poll the order
    after submission and emit a synthetic FillEvent once it transitions
    to FILLED. Microservices-friendly: today this is a coroutine
    polling REST; tomorrow it's a webhook consumer.

Safety story (mirrors the .NET T212Client comment):
  - Defaults to DEMO base URL (`https://demo.trading212.com/api/v0/`).
    Set `mode="live"` to point at the real-money endpoint.
  - In `live` mode the router ALSO requires `allow_real_orders=True`
    AND the env var `TRADEPRO_T212_ALLOW_LIVE=1`. Either missing →
    every order is logged + rejected with a loud reason. This is the
    "no real-money trade from a misconfigured run" safeguard.
  - Demo mode happily places trades against the T212 practice account.

Auth: T212 supports two schemes (Basic with key+secret OR raw
Authorization header with single key). Same picking rule as the .NET
client: secret set → Basic, otherwise raw header.

Credentials come from env vars by default (kept out of git):
    TRADEPRO_T212_API_KEY
    TRADEPRO_T212_API_SECRET   (optional, older accounts)
    TRADEPRO_T212_MODE         (`demo` (default) | `live`)
    TRADEPRO_T212_ALLOW_LIVE   (must equal "1" to send live orders)
You can also pass them to the constructor for tests.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..messages import (
    BarEvent,
    FillEvent,
    OrderApproved,
    ShutdownEvent,
)
from ..router import OrderRouter
from ..strategy import Fill, OrderSide, OrderType


log = logging.getLogger("tradepro.paper.t212_router")


T212_DEMO_BASE = "https://demo.trading212.com/api/v0/"
T212_LIVE_BASE = "https://live.trading212.com/api/v0/"


@dataclass
class T212OrderRouter(OrderRouter):
    """Real-broker router for Trading 212.

    The router consumes `OrderApproved`, POSTs `/equity/orders/market`,
    polls the resulting order until it's terminal, and emits one
    `FillEvent` per fill. Multi-leg fills are coalesced into one Fill
    keyed by the broker order id (T212 doesn't expose partial-fill
    streams on the public API).

    Bar queue: ignored. Kept on the signature so the engine wiring
    stays uniform across routers; T212 fills are driven by REST polling
    rather than bar-edge simulation.
    """

    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    mode: str = "demo"                  # "demo" | "live"
    allow_real_orders: bool = False
    base_url: Optional[str] = None
    poll_seconds: float = 1.0           # T212 rate limit is 1 req / 1s
    timeout_seconds: float = 10.0
    name: str = "t212_router"
    # Tracks orders we've POSTed but not yet seen filled. Keyed by the
    # T212 order id so a restart on the same session can resume polling.
    _pending: dict[int, OrderApproved] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("TRADEPRO_T212_API_KEY")
        if self.api_secret is None:
            self.api_secret = os.environ.get("TRADEPRO_T212_API_SECRET")
        env_mode = os.environ.get("TRADEPRO_T212_MODE")
        if env_mode:
            self.mode = env_mode
        if self.base_url is None:
            self.base_url = T212_LIVE_BASE if self.mode == "live" else T212_DEMO_BASE

    def _live_orders_enabled(self) -> bool:
        """Two-key safety gate. Demo mode always-on. Live mode requires
        constructor flag AND env flag — same posture as the .NET
        backend's "order placement intentionally off"."""
        if self.mode != "live":
            return True
        return (
            self.allow_real_orders
            and os.environ.get("TRADEPRO_T212_ALLOW_LIVE") == "1"
        )

    async def run(
        self,
        approved_queue: asyncio.Queue,
        bar_queue: asyncio.Queue,
        fill_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if not self.api_key:
            log.error(
                "T212OrderRouter started without TRADEPRO_T212_API_KEY — "
                "no orders will be placed. Rejecting all approvals."
            )
        if not self._live_orders_enabled():
            log.warning(
                "T212OrderRouter is in %s mode without the live-trading "
                "gate enabled. Orders will be LOGGED, NOT PLACED.",
                self.mode,
            )

        # Drain bars silently so the engine's bar-fanout doesn't block.
        bar_drain = asyncio.create_task(self._drain_bars(bar_queue))
        try:
            while True:
                msg = await approved_queue.get()
                if isinstance(msg, ShutdownEvent):
                    await fill_queue.put(ShutdownEvent(reason="t212 shutdown"))
                    return
                assert isinstance(msg, OrderApproved)
                await self._handle_approval(msg, fill_queue)
        finally:
            if not bar_drain.done():
                bar_drain.cancel()

    async def _drain_bars(self, bar_queue: asyncio.Queue) -> None:
        while True:
            msg = await bar_queue.get()
            if isinstance(msg, ShutdownEvent):
                return

    async def _handle_approval(
        self,
        approval: OrderApproved,
        fill_queue: asyncio.Queue,
    ) -> None:
        order = approval.order
        if order.type != OrderType.MARKET:
            log.warning(
                "T212OrderRouter only supports MARKET orders today; "
                "got %s for %s. Rejecting.",
                order.type.value, order.symbol,
            )
            return
        if not self._live_orders_enabled() or not self.api_key:
            log.info(
                "T212 WOULD-PLACE · sid=%s · %s %s qty=%s tag=%s",
                order.strategy_id, order.side.value, order.symbol,
                order.quantity, order.tag,
            )
            return

        try:
            t212_order = await self._place_order(order)
        except Exception:
            log.exception("T212 order POST failed for %s", order.symbol)
            return

        order_id = t212_order.get("id")
        if order_id is None:
            log.warning("T212 returned an order without id: %s", t212_order)
            return
        self._pending[order_id] = approval
        # Kick off polling for terminal status — runs concurrently so
        # we can keep accepting the next approval immediately.
        asyncio.create_task(
            self._poll_until_terminal(order_id, approval, fill_queue),
            name=f"t212-poll-{order_id}",
        )

    async def _place_order(self, order) -> dict:
        """POST /equity/orders/market — returns the order resource.
        Network errors propagate so the caller can log + skip."""
        # httpx is a soft dependency; only imported when this router
        # actually runs so unit tests for the rest of the engine don't
        # need it installed.
        import httpx
        payload = {
            "ticker": _to_t212_ticker(order.symbol),
            "quantity": float(order.quantity)
            * (1 if order.side == OrderSide.BUY else -1),
        }
        headers = self._auth_headers()
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout_seconds
        ) as client:
            resp = await client.post(
                "equity/orders/market", json=payload, headers=headers,
            )
            if not resp.is_success:
                # Surface T212's structured error so the operator can
                # tell INSUFFICIENT_FUNDS apart from MARKET_CLOSED
                # apart from INSTRUMENT_NOT_FOUND — without this, every
                # 4xx looks the same.
                body = resp.text[:500] if resp.text else "(empty body)"
                log.error(
                    "T212 order POST %s → HTTP %s · payload=%s · response=%s",
                    resp.url, resp.status_code, payload, body,
                )
                resp.raise_for_status()
            return resp.json()

    async def _poll_until_terminal(
        self,
        order_id: int,
        approval: OrderApproved,
        fill_queue: asyncio.Queue,
    ) -> None:
        """Poll the order resource until it reaches FILLED / CANCELLED /
        REJECTED. T212 rate-limits at 1 req/s per endpoint so the inner
        loop sleeps at least `poll_seconds` between calls."""
        import httpx
        headers = self._auth_headers()
        while True:
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=self.timeout_seconds
                ) as client:
                    resp = await client.get(
                        f"equity/orders/{order_id}", headers=headers,
                    )
                    resp.raise_for_status()
                    t212_order = resp.json()
            except Exception:
                log.exception("T212 poll failed for order %s", order_id)
                await asyncio.sleep(self.poll_seconds)
                continue
            status = (t212_order.get("status") or "").upper()
            if status == "FILLED":
                fill = self._build_fill(approval, t212_order)
                await fill_queue.put(FillEvent(fill=fill))
                self._pending.pop(order_id, None)
                return
            if status in {"CANCELLED", "REJECTED", "EXPIRED"}:
                log.warning(
                    "T212 order %s terminal=%s — no fill emitted",
                    order_id, status,
                )
                self._pending.pop(order_id, None)
                return
            await asyncio.sleep(self.poll_seconds)

    def _build_fill(self, approval: OrderApproved, t212_order: dict) -> Fill:
        order = approval.order
        # T212's payload uses `filledQuantity` + `filledValue` once
        # the order is terminal. Average fill price = value / qty.
        filled_qty = abs(int(float(t212_order.get("filledQuantity") or order.quantity)))
        filled_value = float(t212_order.get("filledValue") or 0.0)
        avg_price = (
            filled_value / filled_qty if filled_qty > 0 else float(t212_order.get("limitPrice") or 0.0)
        )
        # T212 doesn't surface commission on the order resource — set
        # zero here and let the Ledger / reconciliation step backfill
        # from the account-statement endpoint when that lands.
        return Fill(
            order_id=str(t212_order.get("id")),
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            quantity=filled_qty,
            fill_price=avg_price,
            fill_time=datetime.now(timezone.utc),
            commission=0.0,
        )

    def _auth_headers(self) -> dict:
        """Pick Basic vs raw-Authorization based on whether a secret
        was provided. Mirrors `Trading212Client` (.NET backend)."""
        if not self.api_key:
            return {}
        if self.api_secret:
            token = base64.b64encode(
                f"{self.api_key}:{self.api_secret}".encode()
            ).decode()
            return {"Authorization": f"Basic {token}"}
        return {"Authorization": self.api_key}


def _to_t212_ticker(symbol: str) -> str:
    """Convert a Yahoo-style symbol to a T212 ticker.

    Yahoo uses "AAPL", T212 uses "AAPL_US_EQ". The mapping table is
    deliberately tiny — the Production move is to fetch the full
    /equity/metadata/instruments registry once per session and look
    up by name. For now: the common US-equity suffix covers the ORB
    use case (large-cap liquid US names).

    Non-US symbols (LON.L, EUR.PA) should NOT pass through this
    function unchanged — they need explicit per-venue mapping. Raise
    loudly so we don't accidentally route to the wrong instrument.
    """
    if "_" in symbol:
        return symbol  # already a T212 ticker
    if symbol.isascii() and symbol.replace(".", "").isalnum() and "." not in symbol:
        return f"{symbol}_US_EQ"
    raise ValueError(
        f"T212 ticker mapping not configured for {symbol!r}. "
        f"Wire the /equity/metadata/instruments lookup before trading non-US symbols."
    )


__all__ = ["T212OrderRouter"]
