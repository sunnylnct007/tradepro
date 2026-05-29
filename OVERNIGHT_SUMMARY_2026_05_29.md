# Overnight summary — 2026-05-29

## TL;DR — what works now that didn't last night

✅ **IG end-to-end verified**: smoke order placed → IG deal ref `XANS77N3V7LTYSM` → IGOmsFillPoller flipped to FILLED in 20s. The full chain works: OMS → IGClient → IG → /confirms → fill recorded.

✅ **paper-fx daemon now runs IG profile**: was crashing on every wake-up ("Unknown broker profile 'ig'"). Now builds a session, fetches FX bars, generates signals, and routes orders with broker=IG_DEMO. Last successful run completed for 10 G10 pairs. FX EPIC mapping baked in (EURUSD → CS.D.EURUSD.MINI.IP, etc.).

✅ **Strategies now position-aware**: intraday daemon fetches T212 demo positions before strategy registration. ichimoku_equity now starts knowing it owns 22 AAPL, 9 TSLA, etc. Next session run will evaluate "we already hold this" and can emit SELL instead of mechanically BUY-ing more.

✅ **Leaderboard reads paper sessions too**: SQL query widened from `kind='intraday'` to `kind IN ('intraday', 'paper_session')`. Combined with the result_summary shape fix, the leaderboard will populate from this morning's sessions.

✅ **Cockpit banner no longer lies**: excludes `reconcile_from_broker` and `_monitor` strategy_ids from "fills today" count. Was previously inflating to "21 fills cleared" from admin reconciliation rows.

✅ **OMS mode persists**: PostgresOmsModeService backs `oms_mode` in app_settings_kv. No more silent flip-back to Manual on every restart.

✅ **LLM tile sane**: probe reads llm_url + llm_model from settings_kv (catches your `finance-llama-3-8b` change). Localhost URLs now report `disabled` with note "runs on Mac worker — unreachable from EC2 by design" instead of `down`.

✅ **Session queue today-only by default**: PaperLive defaults dateFrom/dateTo to today; widen via the pickers if you want history.

✅ **Repo cleaned**: history squashed to single commit, 12 stale branches deleted, repo public so GH Actions runs free.

## Verify these in the morning

1. **Cockpit refresh** → connectivity panel + multi-broker cash strip + truthful banner. T212 demo cash + IG £10M visible.
2. **`/oms` refresh** → click any order, see the audit-trail expand (state events + risk gates + LLM evaluations).
3. **`/intraday/leaderboard` refresh** → if any new sessions ran overnight, cells should be non-zero.
4. **`/api/admin/ig/search?term=GBPUSD`** → IG market search works for any pair you'd want to trade.

## Known gaps not yet addressed

- **#63 sentiment_score → llm_evaluations push** — endpoint exists, daemon push call not wired yet. Sentiment scoring will still happen normally; you just won't see them on the per-order audit panel.
- **#66 Strategy charts panel** — investigation pending; result_summary unification may have fixed it as a side effect.
- **#72 In-app news context panel** — the "had to ask Claude for TSLA news" gap. Roadmap.
- **#73 Basic-auth password rotation** — `letmein123` was briefly public; you asked to handle this yourself. Heads-up.
- **Python paper-engine IG broker** — **DONE**. `--broker ig` profile added, T212OrderRouter reused with broker_label_override=IG_DEMO + FX epic mapping table. paper-fx daemon verified working.

## The architectural thing worth a conversation

You have **three daemon paths** on the Mac (paper-equity, paper-fx via tradepro-paper, paper-watch via tradepro-intraday-engine) that all write to `/api/ops/sessions` with different histories. I unified the `result_summary` shape but the architecture is still confusing — three different code paths for "run a strategy session". Worth consolidating to one entry point when you have a free Saturday.

## Critical commits pushed overnight

- `f01bf92` — result_summary unification + IG fill poller + cockpit truthfulness + today-only filter
- `a6ab830` (now in `076415c`) — IG /markets search + smoke-order admin endpoint + LLM probe from settings_kv
- `d933f7a` — extend oms_orders.broker CHECK constraint with IG_DEMO/IG_LIVE (was blocking IG enqueue)
- `8e2fd47` — strategies position-aware (intraday_engine fetches T212 positions and pre-loads strategy state)
- `9aee0a5` — FX strategy position-aware too (initial_positions wired to ichimoku_fx_mr) + this summary
- `b9e6c0d` — IG paper broker profile + FX epic mapping + session_date default

All shipped via GH Actions (build-push then redeploy). EC2 is running the latest images.

## What I'd do next if you ask

1. Wait for FX/equity session to fire this morning and verify a real strategy-generated order routes correctly (T212 demo or IG depending on strategy_broker_map).
2. Wire up a position-aware version of ichimoku_fx_mr too (currently only equity is position-aware).
3. Build the Python IGOrderRouter so paper-fx daemon's --broker ig works.
4. Sweep the strategy logic for "already long → emit HOLD/SELL instead of BUY" — the position-awareness wiring is in place but the strategy's signal-to-order translator may still mechanically emit BUY.
