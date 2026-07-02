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


# Account-state cache: NLV / cash / unrealised + per-position P&L + the session's
# fills + broker-golden daily P&L. The desks' account-state push reads THIS
# instead of opening its own (contending) connection — and since the gateway is
# now the connection that PLACES orders, ib.fills() here sees every fill, which
# is exactly what reconciles the clones' OMS orders to FILLED.
ACCOUNT_CACHE = GATEWAY_CACHE.parent / "_gateway_account.json"


async def poll_account_state(ib, want_account: str) -> dict:
    """Read NLV/cash/unrealised + position book (with P&L) + session fills +
    daily P&L for `want_account`, on the gateway's ONE connection. Mirrors the
    shape the desk's account-state push used to read directly."""
    positions = []
    for it in ib.portfolio():
        if want_account and (it.account or "").strip() != want_account:
            continue
        c = it.contract
        positions.append({
            "symbol": c.symbol,
            # secType is the broker-golden asset class (STK / OPT / FUT). Carrying
            # it lets the cockpit tell a deliberate short PUT (the wheel) apart from
            # a genuine equity short WITHOUT reverse-engineering the 100x option
            # multiplier from marketValue — the real field, not a heuristic.
            "secType": getattr(c, "secType", None) or None,
            "multiplier": getattr(c, "multiplier", None) or None,
            "right": getattr(c, "right", None) or None,   # 'P' / 'C' for options
            "strike": getattr(c, "strike", None) or None,
            "expiry": getattr(c, "lastTradeDateOrContractMonth", None) or None,
            "qty": it.position,
            "mark": it.marketPrice,
            "marketValue": it.marketValue,
            "avgCost": it.averageCost,
            "unrealisedPnl": it.unrealizedPNL,
            "currency": getattr(c, "currency", None),
        })
    vals = ib.accountValues(want_account) if want_account else ib.accountValues()

    def _val(tag):
        for v in vals:
            if v.tag == tag and (not want_account or v.account == want_account):
                try:
                    return float(v.value)
                except (TypeError, ValueError):
                    return None
        return None

    def _ccy(tag):
        for v in vals:
            if v.tag == tag and (not want_account or v.account == want_account):
                return v.currency or None
        return None

    fills = []
    for f in ib.fills():
        ex = getattr(f, "execution", None)
        if ex is None:
            continue
        if want_account and (getattr(ex, "acctNumber", "") or "").strip() != want_account:
            continue
        try:
            fills.append({
                "symbol": f.contract.symbol,
                "side": "BUY" if ex.side == "BOT" else "SELL",
                "qty": abs(float(ex.shares)),
                "price": float(ex.price),
            })
        except (TypeError, ValueError, AttributeError):
            continue

    unrl = _val("UnrealizedPnL")
    if unrl is None and positions:
        unrl = sum(p["unrealisedPnl"] for p in positions if p.get("unrealisedPnl") is not None)

    daily_pnl = None
    try:
        pnl = ib.reqPnL(want_account) if want_account else None
        if pnl is not None:
            await asyncio.sleep(2.0)
            dp = getattr(pnl, "dailyPnL", None)
            if dp is not None and dp == dp:  # not None / not NaN
                daily_pnl = float(dp)
            try:
                ib.cancelPnL(want_account)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        daily_pnl = None

    return {
        "broker": "IBKR_PAPER",
        "account_id": want_account or None,
        "currency": _ccy("NetLiquidation"),
        "net_liquidation": _val("NetLiquidation"),
        "total_cash": _val("TotalCashValue"),
        "unrealised_pnl": unrl,
        "daily_pnl": daily_pnl,
        "positions": positions,
        "fills": fills,
    }


def write_account_cache(payload: dict) -> None:
    try:
        ACCOUNT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNT_CACHE.write_text(json.dumps({"ts": time.time(), **payload}))
    except Exception as e:  # noqa: BLE001
        log.warning("ibkr gateway: account cache write failed: %s", e)


def read_account_state(max_age_s: float = 90.0) -> dict | None:
    """DESK side: the gateway's account-state snapshot, or None if missing/stale.
    Desks read this instead of opening their own connection."""
    try:
        d = json.loads(ACCOUNT_CACHE.read_text())
        if time.time() - float(d.get("ts", 0)) <= max_age_s:
            return d
    except Exception:  # noqa: BLE001
        return None
    return None


# ── PHASE 2 — order routing through the ONE connection ───────────────────────
# Desks used to each open their own ib_insync connection to PLACE orders, which
# contended with this gateway's connection over the single account's budget —
# so one overnight `Error 1100` broke every trading client at once (the morning
# connect-timeouts). Fix: desks submit an order INTENT to a shared inbox; THIS
# gateway (the only connection) places it and writes the fill back to an outbox
# the desk reads. Same file-handoff shape as the position cache above.
ORDER_DIR = Path.home() / ".tradepro" / "cache" / "ibkr-orders"
ORDER_INBOX = ORDER_DIR / "inbox"
ORDER_OUTBOX = ORDER_DIR / "outbox"
_FX_CCYS = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "SEK", "NOK"}


def submit_order_intent(intent: dict) -> str:
    """DESK side: publish an order intent for the gateway to place; returns the
    intent_id. `intent_id` MUST be caller-supplied and deterministic (e.g.
    md5(strategy:symbol:side:qty:bar_ts)) so a rerun of the same approval can't
    double-place — the gateway skips an intent whose result already exists.
    Written via a temp+rename so the gateway never reads a half-written file."""
    ORDER_INBOX.mkdir(parents=True, exist_ok=True)
    iid = str(intent["intent_id"])
    tmp = ORDER_INBOX / f".{iid}.tmp"
    tmp.write_text(json.dumps(intent))
    tmp.rename(ORDER_INBOX / f"{iid}.json")
    return iid


def read_order_result(intent_id: str) -> dict | None:
    """DESK side: the gateway's outcome for an intent, or None if not placed yet."""
    f = ORDER_OUTBOX / f"{intent_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:  # noqa: BLE001
        return None


def _write_result(intent_id: str, result: dict) -> None:
    ORDER_OUTBOX.mkdir(parents=True, exist_ok=True)
    tmp = ORDER_OUTBOX / f".{intent_id}.tmp"
    tmp.write_text(json.dumps(result))
    tmp.rename(ORDER_OUTBOX / f"{intent_id}.json")


def _gw_contract(ib_insync, symbol: str):
    """Forex for a G10 pair (IDEALPRO), else US-equity Stock — same rule the
    desks' _ib_contract uses, replicated here to avoid importing the paper
    package into the gateway daemon."""
    s = symbol.replace("/", "").upper()
    if len(s) == 6 and s[:3] in _FX_CCYS and s[3:] in _FX_CCYS:
        return ib_insync.Forex(s)
    return ib_insync.Stock(symbol, "SMART", "USD")


async def place_one_intent(ib, ib_insync, intent: dict, want_account: str, open_keys: set) -> dict:
    """Place ONE intent on the gateway's connection; return the result dict.
    Dedup against `open_keys` (broker is golden) so a rerun never stacks a
    duplicate. TIF is set EXPLICITLY (equities pre-open = OPG auction; forex /
    RTH = DAY) so the Gateway order-preset can't rewrite it mid-submit and
    trigger the Error-10349 cancel→resubmit."""
    symbol = str(intent["symbol"])
    action = str(intent["action"]).upper()
    qty = abs(float(intent["quantity"]))
    account = (intent.get("account") or want_account or "").strip()
    iid = str(intent["intent_id"])
    if (symbol, action) in open_keys:
        return {"intent_id": iid, "status": "skipped_duplicate", "symbol": symbol, "action": action}

    contract = _gw_contract(ib_insync, symbol)
    order = ib_insync.MarketOrder(action, qty)
    if account:
        order.account = account
    tif = "DAY"
    try:
        from datetime import datetime as _dt, timezone as _tz
        from .paper import market_hours
        from .bar_cache.asset_class_resolver import resolve_asset_class
        if getattr(contract, "secType", "") == "STK" and not market_hours.is_open(
                resolve_asset_class(symbol), _dt.now(_tz.utc)):
            tif = "OPG"  # equity opening auction; forex has none
    except Exception:  # noqa: BLE001
        pass
    order.tif = tif

    trade = ib.placeOrder(contract, order)
    open_keys.add((symbol, action))
    boid = str(getattr(trade.order, "orderId", "") or "")
    if tif == "OPG":
        await asyncio.sleep(0.2)  # let it reach PreSubmitted; fills at the auction
        return {"intent_id": iid, "status": "queued_opg", "symbol": symbol,
                "action": action, "broker_order_id": boid}
    waited = 0.0
    while not trade.isDone() and waited < 8.0:
        await asyncio.sleep(0.5)
        waited += 0.5
    filled = sum(float(f.execution.shares) for f in trade.fills) if trade.fills else 0.0
    avg = (sum(float(f.execution.shares) * float(f.execution.price) for f in trade.fills) / filled
           if filled else 0.0)
    return {
        "intent_id": iid, "symbol": symbol, "action": action,
        "status": "filled" if (trade.orderStatus.status == "Filled" or filled > 0) else
                  (trade.orderStatus.status or "working").lower(),
        "broker_order_id": boid, "fill_qty": filled, "fill_price": avg,
    }


async def record_to_oms(intent: dict, result: dict) -> None:
    """Record a gateway-FILLED order into the OMS, attributed to the intent's
    strategy_id — so the clone's trades, per-strategy P&L AND open positions show
    on /oms + the cockpit. The gateway is the ONLY place that has BOTH the fill
    and the strategy_id, so it owns this (the desk's mirror was unreliable — FX
    clone showed 0 OMS orders despite filling). Best-effort + idempotent via a
    deterministic ClientOrderId derived from the intent_id."""
    sid = intent.get("strategy_id")
    fq = float(result.get("fill_qty") or 0)
    fp = float(result.get("fill_price") or 0)
    if not sid or fq <= 0 or fp <= 0:
        return
    try:
        import hashlib
        import uuid as _uuid
        import httpx
        from .secrets import get_secret
        api_base = get_secret("api-base-url")
        api_token = get_secret("api-token")
        if not api_base or not api_token:
            return
        base = api_base.rstrip("/")
        H = {"Authorization": f"Bearer {api_token}"}
        client_id = str(_uuid.UUID(hashlib.md5(str(intent["intent_id"]).encode()).hexdigest()))
        order = {
            "ClientOrderId": client_id, "Broker": "IBKR_PAPER",
            "Symbol": intent["symbol"], "Side": intent["action"],
            "Qty": fq, "OrderType": "MKT", "StrategyId": sid,
            "PlacedBy": "STRATEGY_AUTO", "TimeInForce": "DAY",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{base}/api/oms/orders", json=order, headers=H)
            if r.status_code == 409:
                return  # already recorded this session — idempotent
            if r.status_code not in (200, 201):
                log.warning("gateway→OMS record %s for %s: %s",
                            r.status_code, intent["symbol"], r.text[:120])
                return
            oid = (r.json() or {}).get("id")
            if not oid:
                return
            await client.post(f"{base}/api/oms/orders/{oid}/approve", json={}, headers=H)
            await client.post(
                f"{base}/api/oms/orders/{oid}/fill", headers=H,
                json={"Qty": fq, "Price": fp, "Fee": 0.0, "Currency": "USD",
                      "BrokerFillId": str(result.get("broker_order_id") or client_id)})
        log.info("gateway→OMS: recorded FILLED %s %s qty=%s @ %s (sid=%s)",
                 intent["action"], intent["symbol"], fq, fp, sid)
    except Exception as e:  # noqa: BLE001
        log.warning("gateway→OMS record failed for %s: %s", intent.get("symbol"), e)


async def drain_order_inbox(ib, ib_insync, want_account: str, open_keys: set) -> int:
    """Place every pending inbox intent; write each result to the outbox and
    remove the inbox file. Idempotent (skips an intent already in the outbox);
    a bad/failing intent is errored to the outbox, never blocking the rest."""
    if not ORDER_INBOX.exists():
        return 0
    placed = 0
    for f in sorted(ORDER_INBOX.glob("*.json")):
        try:
            intent = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            f.unlink(missing_ok=True)
            continue
        iid = str(intent.get("intent_id") or f.stem)
        if (ORDER_OUTBOX / f"{iid}.json").exists():
            f.unlink(missing_ok=True)
            continue
        try:
            result = await place_one_intent(ib, ib_insync, intent, want_account, open_keys)
        except Exception as e:  # noqa: BLE001
            result = {"intent_id": iid, "status": "error", "error": str(e),
                      "symbol": intent.get("symbol"), "action": intent.get("action")}
        _write_result(iid, result)
        f.unlink(missing_ok=True)
        placed += 1
        log.info("ibkr gateway: placed intent %s (%s %s) -> %s",
                 iid, intent.get("action"), intent.get("symbol"), result.get("status"))
        # Attribute the fill back to the OMS per strategy_id so the clone's
        # trades + P&L + open positions are visible (immediate market fills;
        # OPG/auction fills reconcile via the account-state fills path).
        if result.get("status") == "filled":
            await record_to_oms(intent, result)
    return placed


async def run_gateway(interval: float = 30.0, iterations: int | None = None) -> None:
    """Connect ONCE, then on each ~1s tick: drain the order inbox (low-latency
    placement) and, every `interval`s, refresh the positions cache. Reconnects
    on failure. `iterations` bounds the loop for tests (None = forever)."""
    from ib_insync import IB

    host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
    # Dedicated gateway clientId — distinct from bus(17)/harvest(18)/trade(21+).
    cid = int(os.environ.get("TRADEPRO_IBKR_GATEWAY_CLIENT_ID", "19"))
    want = (os.environ.get("TRADEPRO_IBKR_ACCOUNT") or "").strip()

    ib = IB()
    import ib_insync as _ibis
    open_keys: set = set()
    last_poll = 0.0
    n = 0
    while iterations is None or n < iterations:
        n += 1
        try:
            if not ib.isConnected():
                await ib.connectAsync(host, port, clientId=cid, timeout=15)
                log.info("ibkr gateway connected %s:%s clientId=%s account=%s",
                         host, port, cid, want or "(any)")
                # Seed the dedup set from the broker's live open orders (golden
                # source) so a placement after a reconnect can't double an order
                # already resting in the auction.
                open_keys = set()
                try:
                    for tr in await ib.reqAllOpenOrdersAsync():
                        open_keys.add((tr.contract.symbol, tr.order.action))
                except Exception:  # noqa: BLE001
                    pass
            # 1) Orders FIRST, every tick — placement must be low-latency.
            await drain_order_inbox(ib, _ibis, want, open_keys)
            # 2) Positions + account-state cache on the slower `interval` cadence.
            now = time.time()
            if now - last_poll >= interval:
                await asyncio.sleep(1.0)  # let position snapshots arrive
                rows = await poll_positions(ib, want)
                write_cache(rows, want)
                # Account-state (NLV/cash/unrealised + P&L + fills) on the same
                # connection — desks read this instead of connecting themselves.
                try:
                    acct = await poll_account_state(ib, want)
                    write_account_cache(acct)
                    log.info("ibkr gateway: cached %d positions + account-state "
                             "(NLV=%s, %d fills)", len(rows),
                             acct.get("net_liquidation"), len(acct.get("fills") or []))
                except Exception as e:  # noqa: BLE001
                    log.warning("ibkr gateway: account-state poll failed: %s", e)
                    log.info("ibkr gateway: cached %d positions", len(rows))
                last_poll = now
        except Exception as e:  # noqa: BLE001
            log.warning("ibkr gateway loop failed (will reconnect): %s", e)
            try:
                ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if iterations is None or n < iterations:
            await asyncio.sleep(1.0)  # tick cadence (orders drained ~1s)
    try:
        ib.disconnect()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_gateway())


if __name__ == "__main__":
    main()
