"""IBKR connection gateway — ONE persistent connection, serving all consumers.

The contention root-cause (see ROADMAP § 2026-06-14, [[ibkr_harvest_session_isolation]]):
every desk + the harvest opens its OWN connection to the one IBKR paper account and
fights over its per-account budget, so `reqPositions` times out under load and the
desks fail-close (the ×32/×36 aborts). This service is the fix: a SINGLE persistent
connection that polls positions + account state and writes a shared cache; every desk
reads the cache instead of connecting to IBKR itself → exactly one consumer, no contention.

PHASE 1 (this file): the positions/account poller + shared cache. Desks read the cache
(prefer-fresh, fall back to a direct read). PHASE 2: bars on a paced queue + orders routed
through here, generalising the existing `IBKRBarBus`.

NOTE: `reqPositions` / account updates are ACCOUNT data, not MARKET data — so this poller
does NOT need a market-data subscription and won't hit Error 162 (which is market-data).
That's why a single positions-gateway is reliable even when market-data is contended.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

# Shared cache the desks read (same dir as the per-strategy resilience caches).
GATEWAY_CACHE = Path.home() / ".tradepro" / "cache" / "ibkr-positions" / "_gateway.json"

log = logging.getLogger("tradepro.ibkr_gateway")


async def poll_positions(ib, want_account: str) -> list[dict]:
    """Effective book = filled positions + PENDING (working/OPG) orders, scoped to
    `want_account`. Mirrors the desks' `_fetch_ibkr_rows` logic so the gateway's
    snapshot is identical to what a desk would read itself."""
    eff: dict[str, float] = {}
    avg: dict[str, float] = {}
    for pp in ib.positions():
        if want_account and (pp.account or "").strip() != want_account:
            continue
        s = pp.contract.symbol
        eff[s] = eff.get(s, 0.0) + pp.position
        if pp.avgCost:
            avg[s] = pp.avgCost
    try:
        await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), 6)
    except Exception:  # noqa: BLE001 — best-effort; positions alone are still useful
        pass
    await asyncio.sleep(0.5)
    for t in ib.openTrades():
        if t.orderStatus.status in ("Cancelled", "Filled", "Inactive", "ApiCancelled"):
            continue
        if want_account and (t.order.account or "").strip() != want_account:
            continue
        s = t.contract.symbol
        eff[s] = eff.get(s, 0.0) + t.order.totalQuantity * (1 if t.order.action == "BUY" else -1)
    return [
        {"ticker": s, "quantity": q, "averagePricePaid": avg.get(s, 0.0)}
        for s, q in eff.items() if q != 0
    ]


def write_cache(rows: list[dict], account: str) -> None:
    try:
        GATEWAY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        GATEWAY_CACHE.write_text(json.dumps({"ts": time.time(), "rows": rows, "account": account}))
    except Exception as e:  # noqa: BLE001
        log.warning("ibkr gateway: cache write failed: %s", e)


async def run_gateway(interval: float = 30.0, iterations: int | None = None) -> None:
    """Connect once, then poll positions every `interval`s and refresh the shared
    cache. Reconnects on failure. `iterations` bounds the loop for tests (None = forever)."""
    from ib_insync import IB

    host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
    # Dedicated gateway clientId — distinct from bus(17)/harvest(18)/trade(21+).
    cid = int(os.environ.get("TRADEPRO_IBKR_GATEWAY_CLIENT_ID", "19"))
    want = (os.environ.get("TRADEPRO_IBKR_ACCOUNT") or "").strip()

    ib = IB()
    n = 0
    while iterations is None or n < iterations:
        n += 1
        try:
            if not ib.isConnected():
                await ib.connectAsync(host, port, clientId=cid, timeout=15)
                log.info("ibkr gateway connected %s:%s clientId=%s account=%s",
                         host, port, cid, want or "(any)")
            await asyncio.sleep(1.0)  # let snapshots arrive
            rows = await poll_positions(ib, want)
            write_cache(rows, want)
            log.info("ibkr gateway: cached %d positions", len(rows))
        except Exception as e:  # noqa: BLE001
            log.warning("ibkr gateway poll failed (will reconnect): %s", e)
            try:
                ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if iterations is None or n < iterations:
            await asyncio.sleep(interval)
    try:
        ib.disconnect()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_gateway())


if __name__ == "__main__":
    main()
