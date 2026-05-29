# Multi-Claude Coordination

Two Claude Code sessions may work on this repo concurrently on the same Mac.
Read this before starting any work. Update when you start something new.

**Last updated: 2026-05-29**

---

## Current branch: feature/intraday-flat  (laneB checkout)

Phase 1 of the `intraday_flat` strategy: the strategy file itself, BDD
scenarios, trader operating notes in STRATEGIES.md. Built on top of
the phase-0 plumbing already on `main` (commit `076415c`).

This lane runs in the dedicated `tradepro-laneB/` checkout so it cannot
collide with whatever the `tradepro/` checkout is doing.

---

## Active work

**Lane B: phase-1 strategy file for `intraday_flat`** — the actual
intraday EOD-flat strategy that uses the phase-0 plumbing already on
main. Long-only, scanner-derived basket, LLM-gated entries, ATR-anchored
stops, three-layer EOD flatten defense, full decision-log audit at
every gate.

Files touched on `feature/intraday-flat` in `tradepro-laneB/`:

- `strategies/tradepro_strategies/paper/strategies/intraday_flat.py` —
  the `IntradayFlatStrategy` class. Registered as `intraday_flat`.
- `strategies/features/intraday_flat.feature` — 16 BDD scenarios
  covering scanner, entry pipeline, LLM gate, position management,
  EOD flatten. All green; no regressions in `paper_quant_strategies`.
- `strategies/features/steps/intraday_flat_steps.py` — step impls.
- `STRATEGIES.md` — new "Intraday EOD-Flat with daily-Ichimoku basket"
  subsection under Layer 2.
- `COORDINATION.md` — this entry.

Lane A (separate checkout `tradepro/`) at the same time was extending
the IG backend (`/api/admin/ig/search`, `/api/admin/ig/smoke-order`)
and bundled the phase-0 Python plumbing into commit `076415c`.

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
