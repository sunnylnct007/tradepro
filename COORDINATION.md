# Multi-Claude Coordination

Two Claude Code sessions are working on this repo concurrently on the
same Mac. We can't message each other directly — this file is the
shared whiteboard. Both sessions: read this before starting work,
update when you start a new lane.

## Lanes (as of 2026-05-25)

### Lane A — Quant Engine + Paper Bridge + LLM Gate (branch `feat/quant-engine`)
- **Owner**: Lane A Claude session
- **Status**: **PR #14 OPEN — ready to merge** (https://github.com/sunnylnct007/tradepro/pull/new/feat/quant-engine)
- **Commits on branch** (5 commits ahead of main):
  - `412e160` quant_engine — Ichimoku sleeve, vol targeting, walk-forward, Monte Carlo, FX mean-reversion
  - `ad08e3c` paper trading bridge — IchimokuEquityStrategy, IchimokuFXMeanReversionStrategy, OverrideRegistry, BrokerFactory, signal_bridge
  - `b73ff35` LLM signal gate, StrategyConfigRegistry, StrategyRunner, 5 MCP tools
  - `cb317ca` LLM gate wired into both strategies (579 BDD scenarios green)
- **Files owned by Lane A** (do NOT touch):
  - `strategies/tradepro_strategies/quant_engine/` (10 files)
  - `strategies/tradepro_strategies/paper/llm_gate.py`
  - `strategies/tradepro_strategies/paper/overrides.py`
  - `strategies/tradepro_strategies/paper/signal_bridge.py`
  - `strategies/tradepro_strategies/paper/broker_factory.py`
  - `strategies/tradepro_strategies/paper/strategy_config.py`
  - `strategies/tradepro_strategies/paper/strategy_runner.py`
  - `strategies/tradepro_strategies/paper/strategies/ichimoku_equity.py`
  - `strategies/tradepro_strategies/paper/strategies/ichimoku_fx_mr.py`
  - `strategies/features/paper_quant_strategies.feature`
  - `strategies/features/quant_engine.feature`
  - `strategies/features/llm_signal_gate.feature`
  - `docs/QUANT_ENGINE_GAPS.md`
- **Immediate next step** (one session after PR merges):
  Add `--strategy ichimoku_equity|ichimoku_fx_mr` flag to
  `cli/paper_session.py` so the quant strategies can be launched from the
  terminal against T212 demo. Engine + BarBus infrastructure is already in
  place — this is a ~2h wiring task.

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
- 2026-05-25 — Lane A **completed paper trading stack** (PR #14 opened).
  5 commits: quant engine → paper bridge → LLM gate → LLM gate wired into
  both strategies. 579/579 BDD green. Branch pushed as
  `feat/quant-engine`. NOT touching `feat/fundamental-analysis` or
  `tradepro-laneB/` working tree. Next task: wire `--strategy` flag into
  `cli/paper_session.py` after PR merges.
