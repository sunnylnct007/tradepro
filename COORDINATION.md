# Multi-Claude Coordination

Two Claude Code sessions may work on this repo concurrently on the same Mac.
Read this before starting any work. Update when you start something new.

**Last updated: 2026-05-29**

---

## Current branch: feature/broker-mapping-ui  (laneB checkout)

Follow-up on the `intraday_flat` strategy (phase-1 shipped in PR #28 /
commit `6f58920`). Adds overnight-leftover handling + 8 BDD scenarios
that fill gaps surfaced by the laneA position-aware-session-start work
(commits `8e2fd47` + `87a1258`).

This lane runs in the dedicated `tradepro-laneB/` checkout so it cannot
collide with whatever the `tradepro/` checkout is doing.

---

## Active work

**Lane B: intraday_flat follow-up** — bring intraday_flat in line with
the `seed_positions()` + `params.initial_positions` contract that
laneA introduced for ichimoku_equity. For an EOD-flat strategy, a
seeded position means the prior session's flatten failed — log a clear
alert and force-flatten on the first in-window bar. Also fills 7 other
test-coverage gaps surfaced from the full BDD audit:

- `seed_positions()` direct call + `alert-overnight-leftover` logging
- `params.initial_positions` handled in `on_session_start`
- Overnight leftover flattened on first in-window bar (`OVERNIGHT-LEFTOVER` tag)
- Off-basket leftover still flattens (off-basket guard updated for
  held positions)
- In-flight guard blocks duplicate emits before the fill lands
- Max-positions cap blocks new entries
- `on_fill` re-anchors stop/target to the actual fill price (not bar.close)
- LLM gate that raises is fail-open APPROVED (and the order tag handles
  the gate_decision=None case explicitly — a real bug found by the test)

Files touched on `feature/intraday-flat-followup` in `tradepro-laneB/`:

- `strategies/tradepro_strategies/paper/strategies/intraday_flat.py` —
  `seed_positions()` method, `initial_positions` handling, overnight
  leftover force-flatten path, gate_decision=None tag fix, caveat
  entry for leftover behaviour.
- `strategies/features/intraday_flat.feature` — 8 new scenarios in
  Sections 6, 7, 8.
- `strategies/features/steps/intraday_flat_steps.py` — step impls.
- `COORDINATION.md` — this entry.

Test status: **24 / 24 intraday_flat** scenarios green;
**677 / 677 repo scenarios green**.

Lane A (separate checkout `tradepro/`) shipped a busy stretch between
`076415c` and `ddbbda8` — IG broker profile, FX epic mapping, position
seeding from broker, OMS-vs-broker truthfulness, T212 smoke order.
Nothing in this follow-up PR conflicts; the seed_positions hook is the
explicit integration point laneA opened.

---

## Shipped features (on main, newest first)

| Commit | What |
|--------|------|
| `590570a` | SQS trigger queue — real-time Mac↔UI messaging (parked: inert until terraform applied + env var set; REST polling is the active fallback) |
| `8b68505` | Paper scheduling + UI trigger: launchd plists, `tradepro-paper-watch` daemon, `/paper-live` React page, `run-paper` / `poll-paper` backend ops endpoints |
| `4b8485c` | Quant engine + paper bridge + LLM gate: Ichimoku sleeves, vol targeting, walk-forward, Monte Carlo, FX mean-reversion, 579 BDD scenarios green |
| `3952132` | Fundamental analysis + all 7 Track 2 core-portfolio modules + Symbol Analysis Card + MCP `get_symbol_analysis` |

---

## Key files — who last touched what

| Area | Files | Last commit |
|------|-------|-------------|
| Paper strategies | `paper/strategies/ichimoku_equity.py`, `ichimoku_fx_mr.py` | `4b8485c` |
| Paper daemon | `cli/paper_daemon.py` | `590570a` |
| Ops endpoints | `backend/.../Endpoints/OpsEndpoints.cs` | `590570a` |
| SQS service | `backend/.../Providers/SqsTriggerService.cs` | `590570a` |
| MCP server/tools | `mcp/server.py`, `mcp/tools.py` | `8b68505` |
| React Paper page | `frontend/src/pages/PaperLive.tsx` | `8b68505` |
| Terraform (ccit-infra) | `modules/tradepro-demo/main.tf` + variables + outputs | ccit-infra `6c0e302` |
| Quant engine | `quant_engine/` (10 files) | `4b8485c` |
| Core portfolio | `core_portfolio/` (7 modules) | `3952132` |

---

## Rules of engagement

- **Before any `git add`** — run `git status` and confirm you own every file listed.
  Never `git add .` or `git add -A`; always pass explicit file paths.
- **Before commit** — re-run `git status` and verify the staged set is exactly what
  you intended. `git reset HEAD <path>` anything you don't own before committing.
- **One commit at a time** — check `git log -1` to confirm the other session isn't
  mid-commit before you start yours.
- **Starting new work** — add an "Active work" section above with the files you plan
  to touch, commit this file first, then start editing.
- **Reading any file** — always fine; edit with the above protocol.
- **Stashes** — never `git stash drop` a stash you didn't create.
- **ccit-infra** — separate repo at `/Users/skumar/sourcecode/ccit-infra`; note changes
  here just like tradepro changes.

---

## Session log

- 2026-05-25 — Both lanes merged to main. Single stream going forward.
- 2026-05-25 — Shipped: paper scheduling + UI trigger (`8b68505`).
- 2026-05-25 — Shipped: SQS trigger queue (`590570a`, parked — REST polling is live fallback).
- 2026-05-25 — **Session ended. No active edits. All files safe.**
- 2026-05-29 — Started: phase-0 plumbing for `intraday_flat` on
  `feature/intraday-flat-phase0` (Order schema fields + signal-bridge
  ranking helper + IG epic-map loader/seed). Cherry-picked into main
  by the other session as commit `076415c` bundled with their IG
  admin endpoints (`/api/admin/ig/search`, `/api/admin/ig/smoke-order`).
- 2026-05-29 — Started: phase-1 strategy file for `intraday_flat` in
  `tradepro-laneB/` on `feature/intraday-flat`. Full strategy class +
  16 BDD scenarios green + STRATEGIES.md operating notes. Built on
  top of phase-0 plumbing from main.
- 2026-05-29 — Shipped: `intraday_flat` phase-1 merged as PR #28 /
  commit `6f58920`.
- 2026-05-29 — Started: `intraday_flat` follow-up on
  `feature/intraday-flat-followup` — overnight-leftover handling
  (seed_positions + initial_positions) + 8 new BDD scenarios filling
  test coverage gaps. 24/24 intraday_flat green; 677/677 repo green.
- 2026-05-29 — Shipped: `intraday_flat` follow-up merged as PR #29 /
  commit `7d853c1`.
- 2026-05-29 — Started: broker-mapping seed for `intraday_flat` on
  `feature/intraday-flat-broker-mapping` — adds migration 024 with
  `intraday_flat → IG_DEMO` row into the `strategy_broker_map` table
  (introduced by laneA migration 021). Also documents the mapping
  table + resolution priority in STRATEGIES.md so traders / future
  strategies know how to pick a broker.
- 2026-05-29 — Shipped: broker-mapping seed for `intraday_flat`
  merged as PR #30 / commit `3b587d8`.
- 2026-05-29 — Started: broker-mapping UI editor on
  `feature/broker-mapping-ui` — full read/edit surface for
  `strategy_broker_map`. Migration 025 adds CHECK constraint on
  broker values; AdminEndpoints.cs gains GET/PUT/DELETE; new
  StrategyBrokerMapSection.tsx component renders on Settings page
  with effective-broker badge per row, resolution-priority callout,
  per-row save, confirm prompts on broker flips. 22/22 .NET tests +
  677/677 Python BDD green; frontend builds clean.
