"""Permanent broker rejections — remember them, never retry them.

Owner, 2 Sep 2026, looking at the desk: *"mean_reversion_swing_ibkr seems to
have olded startegy symbols traded"*. What that surfaced was worse than a
stale symbol list.

## What happened

The swing daemon placed **28 identical BUY IWM orders on 2026-09-02**, one
every 15 minutes, the last of them 90 minutes after the US close. Every one
came back from IBKR with the same answer:

    "BUY 17 IWM ARCA"
    No Trading Permission, Customer Ineligible; Ineligibility reasons:
    This product does not have a KID in English or in a language approved
    for your country.

That is the UK/EU PRIIPs rule. A UK retail account cannot buy a US-domiciled
ETF — not today, not tomorrow, not ever. It is not a rate limit, not an
outage, not a transient. Retrying it is guaranteed to fail, and the hardcoded
swing list holds FIVE such names (SPY, QQQ, IWM, SOXX, VOO).

Three separate defects had to line up for this to run all day, and any ONE of
them would have stopped it:

1. nothing remembered the rejection            → this module
2. the router treated approve-409 as success   → t212.py, now logs loudly
3. the idempotency seed used the APPROVAL bar,
   which moves every run, not the SIGNAL bar,
   so 28 distinct ClientOrderIds were minted
   for ONE signal bar (2026-09-01)             → t212.py, now seeds off the signal

## Why the OMS is the store, and nothing new is written

The rejections are ALREADY recorded, with their reasons, on the orders the OMS
returns. Inventing a second list would be a second definition of the same fact
— the failure shape this repo hits more than any other. So this asks the OMS
what it already knows.

A reason we do NOT recognise is deliberately treated as TRANSIENT (retryable).
Blocking on an unknown string would silently stop trading a name for a reason
nobody chose, which is the more expensive mistake.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("tradepro.paper.ineligible")

# Substrings (lowercased) that mean THIS WILL NEVER SUCCEED for this account.
# Entitlement and eligibility, not liquidity, not margin, not market hours.
# Keep this list short and evidence-led: every entry should trace to a real
# rejection we have actually seen, quoted in the comment beside it.
PERMANENT_MARKERS: tuple[str, ...] = (
    "no trading permission",        # IBKR, IWM 2026-09-02 (PRIIPs KID)
    "customer ineligible",          # IBKR, same rejection
    "does not have a kid",          # PRIIPs, the specific cause
    "not eligible to trade",        # IBKR variant
    "contract is not available",    # IBKR, delisted / not tradeable
)

_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_TTL_S = 300.0


def is_permanent(reason: str | None) -> bool:
    """True when a broker rejection can never succeed on a retry.

    Unknown reasons return False — see the module docstring. We would rather
    retry something hopeless than silently stop trading a name for a reason
    nobody chose.
    """
    if not reason:
        return False
    low = str(reason).lower()
    return any(m in low for m in PERMANENT_MARKERS)


def blocked_symbols(api_base: str, token: str | None, broker: str,
                    *, timeout: float = 20.0) -> dict[str, str]:
    """{broker_symbol: reason} permanently rejected for `broker`.

    Reads the OMS — the same record the desk shows — so there is exactly one
    definition of "this was rejected". Cached briefly because the daemon runs
    every 15 minutes and this must not become a per-order HTTP call.

    ANY failure returns {} (fail-open). This guard exists to stop a pointless
    retry loop; it must never become a new reason that trading stops. A wedged
    OMS read blocking every order would be a worse outage than the loop.
    """
    key = f"{api_base}|{broker}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL_S:
        return hit[1]

    out: dict[str, str] = {}
    try:
        import requests
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(f"{api_base.rstrip('/')}/api/oms/orders",
                            headers=headers, params={"limit": 1000},
                            timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        rows = body if isinstance(body, list) else (
            body.get("orders") or body.get("items") or body.get("rows") or [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("broker") or "") != broker:
                continue
            if str(row.get("state") or "") != "REJECTED":
                continue
            reason = row.get("cancelledReason")
            if not is_permanent(reason):
                continue
            sym = str(row.get("symbol") or "")
            if sym:
                out.setdefault(sym, str(reason))
    except Exception as exc:  # noqa: BLE001 — fail-open, see docstring
        log.warning("ineligibility check unavailable (%s: %s) — "
                    "orders proceed unguarded this run",
                    type(exc).__name__, str(exc)[:160])
        return {}

    _CACHE[key] = (time.time(), out)
    return out


def first_line(reason: str) -> str:
    """The human half of a broker reason, without the HTML and the FAQ link."""
    txt = " ".join(str(reason).split())
    cut = txt.find(" More information")
    if cut > 0:
        txt = txt[:cut]
    return txt[:200]
