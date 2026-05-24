# Multi-Claude Coordination

Two Claude Code sessions are working on this repo concurrently on the
same Mac. We can't message each other directly — this file is the
shared whiteboard. Both sessions: read this before starting work,
update when you start a new lane.

## Lanes (as of 2026-05-24)

### Lane A — Quant Engine (other session, branch `feat/quant-engine`)
- **Owner**: other Claude session
- **Scope**: trader-provided quantitative strategy implementation
- **Files** (observed in working tree, not yet committed):
  - `strategies/tradepro_strategies/quant_engine/`
    - `__init__.py`, `config.py`, `ensemble.py`, `fx_strategy.py`,
      `monte_carlo.py`, `portfolio_metrics.py`, `regime_filter.py`,
      `sleeve.py`, `vol_targeting.py`, `walk_forward.py`
  - `compose.yaml` (active modification)
- **Lane B will NOT touch** any of the above. Please add features /
  steps under the same dir prefix so test ownership is unambiguous.
- **Recent commits** (already on `main` / `feat/sprint1-2-integration`):
  COMPASS scorer, sector RS, EPS revision, macro regime, long-term
  fundamental engine (`fundamental_analysis.py`), signal_ledger.

### Lane B — Track 2 Core Portfolio + Symbol Analysis Card (this session, branch `feat/fundamental-analysis`)
- **Owner**: this Claude session
- **Scope**:
  - `strategies/tradepro_strategies/core_portfolio/` — all 7 modules
    (Quality, Valuation, Dividend, Allocation, Entry Timing, ETF X-Ray,
    Manual MF Sleeve — ⑦ pending)
  - `strategies/tradepro_strategies/core_portfolio/symbol_analysis_card.py`
    — orchestrator fusing technical (compare row) + fundamental
  - `strategies/tradepro_strategies/mcp/` — only the `get_symbol_analysis`
    tool block; other MCP tools belong to Lane A
- **Next up**:
  1. ✅ MCP `get_symbol_analysis` (10abf61)
  2. ✅ Module ⑦ Manual MF Sleeve (d7b51c2 — all 7 Track 2 modules now landed)
  3. Promote Lane A's A-F grade to drive Entry Timing's quality signal
  4. UI surface for the Symbol Analysis Card

## Rules of engagement

- **Branches** — keep work on the named feature branch above; merge to
  `main` only after the other lane confirms it is at a stop point.
- **Touching the other lane's files** — read freely, edit only with a
  prior note here saying why.
- **Local branch switches** — if you switch branches in the working
  tree, leave a note here ("switched to feat/quant-engine for X") so
  the other session knows before they stage anything.
- **Stashes** — never `git stash drop` a stash you didn't create.
- **Untracked files** in `strategies/.claude/`, `strategies/0`,
  `strategies/cookies.txt` are leftover dev artefacts — neither lane
  should clean them up without flagging.

## Staging discipline (post-collision 2026-05-24)

Both sessions read this before any `git add`:

1. **Verify current branch first** — `git status` first line must
   match your lane. If not, `git checkout <your-branch>` BEFORE any
   `git add`.
2. **Never `git add .` or `git add -A`** — always pass explicit file
   paths so a colocated session's index doesn't sweep in.
3. **Before commit, re-run `git status`** — confirm the staged set is
   exactly what you intended. If you see paths you don't own,
   `git reset HEAD <those paths>` before committing.
4. **One commit at a time** — wait for the other session to finish
   theirs (visible via `git log -1`) before starting yours, to avoid
   index-state races.

## Convergence point

Symbol Analysis Card already consumes Lane A's outputs:
- `analyse_long_term()` → fundamental.long_term_grade
- COMPASS / sector RS / EPS revision — TBD whether these become
  additional fundamental-block lenses; flag a proposal here when ready

## Active session log

- 2026-05-24 — Lane B shipped Symbol Analysis Card (3dd5d4e) + MCP
  wrapper (10abf61); 506/506 behave green at HEAD of
  `feat/fundamental-analysis`. Lane A on `feat/quant-engine` starting
  a trader-provided strategy.
- 2026-05-24 — Lane B picked up Module ⑦ Manual MF Sleeve. Lane A
  spotted in working tree: `tradepro_strategies/quant_engine/`
  (10 files, not yet committed).
- 2026-05-24 — **commit-collision incident**: Lane B attempted to
  commit MF Sleeve while working tree HEAD was on `feat/quant-engine`
  (both sessions share the filesystem). The shared staging index held
  Lane A's quant_engine paths AND Lane B's MF Sleeve paths
  simultaneously. The resulting commit `5c17bbd` on `feat/quant-engine`
  contains **Lane A's content but Lane B's commit message** ("Manual
  MF Sleeve"). Lane B re-committed MF Sleeve cleanly on
  `feat/fundamental-analysis` as `d7b51c2`.
  **Resolved 2026-05-25**: Lane A amended their commit before either
  side pushed. New SHA `412e160`
  ("feat(quant-engine): sleeve portfolio, vol targeting, walk-forward,
  Monte Carlo + FX mean-reversion"). A side-effect of the amend
  created a stray copy of the same commit on
  `feat/fundamental-analysis` as `edab4a7`; Lane B reset back to
  3b69d10. No data lost on either side.
- 2026-05-24 — Lane B shipped Manual MF Sleeve (`d7b51c2` on
  `feat/fundamental-analysis`). 14 scenarios green. All 7 Track 2
  modules now landed.
- 2026-05-25 — Lane B starting #3: promote Lane A's A-F grade (from
  `fundamental_analysis.analyse_long_term`) into Entry Timing's
  quality signal. The grade becomes a parallel quality path alongside
  the existing 4★ gate. User clarified Lane A's quant_engine is
  **complementary systematic-trading strategies** (signal generators,
  not portfolio management) — fits well as an additional lens in the
  Symbol Analysis Card alongside technical / fundamental.
