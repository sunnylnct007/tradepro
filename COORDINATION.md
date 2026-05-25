# Multi-Claude Coordination

Two Claude Code sessions may work on this repo concurrently on the same Mac.
Read this before starting any work. Update when you start something new.

**Last updated: 2026-05-25**

---

## Current branch: main (both lanes merged)

There are no active feature branches. All work lands directly on `main`.
**No active edits in progress.** All files are currently safe to touch.

---

## Active work

**None right now.** If you are starting something, add a section here first,
list the files you will touch, and commit this file before editing anything else.

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
