# IBKR session topology — one Gateway, multiple clients

**Decision (2026-06-11): ONE Gateway, shared by trading + harvesting.** A single
IB Gateway accepts up to ~32 simultaneous API clients, each with a distinct
`clientId`. So the harvester and the trading daemon both connect to the **one
DUP656969 Gateway on port 7500** — which is how it already works. **No separate
account needed.**

> Earlier this doc recommended a separate user + 2nd Gateway. That was
> over-engineered. The "one session per account" limit applies to **logins**
> (you can't run *two Gateways* on the same username — the 2nd kicks out the
> 1st), NOT to API clients. Multiple clients on **one** Gateway is fine, so a
> "session conflict" is almost always a **clientId collision** (two procs on the
> same id, or a crashed proc holding one) — not an account problem. Also: port
> **4002 is already taken** by the local `ecos-strategy` Docker container.

## Topology

| Role | clientId | Gateway | Notes |
|---|---|---|---|
| **Trading** | 21 · seed +100 (121) · acct-state +200 (221) | 7500 | full (orders) |
| **Live bar bus** | 17 | 7500 | read |
| **Harvester** | 18 | 7500 | readonly (`readonly=True`) — can't place orders |

The harvester connects `readonly=True`, so it physically cannot trade even
sharing the Gateway — that's the safety guardrail without a separate account.

## clientId map (keep non-colliding)
`17` live bar bus · `18` harvester · `21` trade main · `+100` position seed ·
`+200` account-state push. Distinct per family; never overlap.

## Setup — nothing to provision

The harvester already defaults to clientId **18** and connects to **7500**
(the trading Gateway), so it just works alongside the trader (21) and bus (17).
Keep one IB Gateway running on 7500 logged into DUP656969.

## Avoiding the only real shared-Gateway risks
1. **Distinct clientIds** (17 bus / 18 harvest / 21 trade / +100 seed / +200
   acct-state) — never let two procs grab the same id. A "session conflict" is
   almost always this.
2. **Clean `disconnect()`** on every path (provider/seed/acct-state use `finally`
   disconnects) so a crashed proc doesn't hold a clientId.
3. **Pacing is per-account** — keep the *heavy* backfill **post-close**
   (`com.tradepro.bar-cache-harvest`, 21:15 UTC ✔). 82 daily symbols is light;
   no contention with live trading.

## Optional future isolation (NOT needed now)
If you ever DO want a fully isolated data session (separate pacing budget),
the code already supports it via `TRADEPRO_IBKR_DATA_HOST/PORT/CLIENT_ID` on the
harvest + data-worker plists (falls back to the shared `TRADEPRO_IBKR_*` when
unset). It needs a **separate IBKR *user* login** (not a sub-account — pacing is
per login) on its **own** Gateway. Pick a free port (**not 4002** — taken by the
`ecos-strategy` Docker container). Unset = shares 7500 (current, recommended).

See memory `project_ibkr_harvest_session_isolation`, `project_ibkr_paper_clone`.
