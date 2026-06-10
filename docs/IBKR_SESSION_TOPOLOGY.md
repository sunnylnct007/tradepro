# IBKR session topology — trading vs. data isolation

**Why:** the bar harvester and the trading daemon currently share **one IBKR
Gateway / one account** (DUP656969, port 7500). IBKR pacing + market-data
limits are **per login**, not per clientId or sub-account — so heavy historical
harvesting contends with live execution on the same budget (pacing violations,
single point of failure). Fix = run harvesting on a **separate IBKR user +
Gateway**.

> Key IBKR fact: a **sub-account** under the same master login does **not**
> isolate the API session/pacing. You need a **separate USER login** (own
> username) — which a company/institutional master can provision as an extra
> user with role-based permissions.

## Target topology

| Role | IBKR user/login | Gateway port | clientId(s) | Permissions |
|---|---|---|---|---|
| **Trading** | DUP656969 (current) | **7500** | 21 · seed +100 (121) · acct-state +200 (221) | full (orders) |
| **Live bar bus** | DUP656969 | 7500 | 17 | read |
| **Data / harvest** | **new read-only user** | **4002** (new Gateway) | 18 | market-data + **read-only** |

The data user being **read-only** is a safety guardrail — the harvester
physically cannot place an order even if mis-configured.

## clientId map (keep non-colliding)
`17` live bar bus · `18` harvester · `21` trade main · `+100` position seed ·
`+200` account-state push. Distinct per family; never overlap.

## Setup steps

**IBKR side (manual):**
1. Under the company master, **add a user** scoped to market-data + read-only
   (no trading).
2. Confirm **historical/daily market-data entitlements** for that user
   (daily bars usually serve without a real-time sub; real-time / 1-min may
   need a subscription assigned).
3. Run a **second IB Gateway** (IBC) logged into the data user, API on a
   distinct port (e.g. **4002**).

**Code side (done — one-env flip):**
The harvester (`bar_cache/providers/ibkr_provider.py`) and the data-worker now
prefer a dedicated data connection, falling back to the shared vars:

```
TRADEPRO_IBKR_DATA_HOST       # preferred (data Gateway host)
TRADEPRO_IBKR_DATA_PORT       # preferred (e.g. 4002)
TRADEPRO_IBKR_DATA_CLIENT_ID  # preferred (e.g. 18)
# fallback (legacy / trading): TRADEPRO_IBKR_HOST / _PORT / _CLIENT_ID (7500)
```

To isolate, set on the harvest + data-worker launchd plists only:
```
TRADEPRO_IBKR_DATA_PORT=4002
TRADEPRO_IBKR_DATA_CLIENT_ID=18
```
Unset → harvesting shares the trading Gateway (legacy behaviour, unchanged).

## Belt-and-braces (regardless)
- Keep the heavy scheduled harvest **post-close** (`com.tradepro.bar-cache-harvest`, 21:15 UTC). ✔
- Route **on-demand** data-worker IBKR fetches to the data Gateway too (same env).
- Ensure clean `disconnect()` on every IBKR path (provider/seed/acct-state use
  `finally` disconnects) so a hung process doesn't hold a clientId.

See memory `project_ibkr_harvest_session_isolation`, `project_ibkr_paper_clone`.
