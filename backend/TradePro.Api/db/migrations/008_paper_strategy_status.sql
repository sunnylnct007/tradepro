-- ── paper_strategy_status ────────────────────────────────────────────
-- Runtime overrides for a strategy's promotion-lifecycle status. The
-- code-level default ships in Strategy.status (Python ClassVar), but
-- operators promote via the UI without redeploying — those overrides
-- land here and shadow the code default on read.
--
-- Lifecycle (matches Strategy.status valid values):
--   evaluating     — newly landed, only manual UI triggers allowed
--   backtest-ok    — past N successful backtests, manual UI still
--   scheduled      — launchd / cron schedules may run it
--   live-eligible  — OMS auto-mode may post live orders
--
-- One row per strategy_id (PK). Promotion = UPSERT. No history
-- captured here; if we want an audit trail later, add a sibling
-- paper_strategy_status_events table along the same pattern as
-- session_requests (the OMS event-sourcing model).
--
-- Read merge: PaperStrategyStatusStore.Get returns this row's status
-- when present; otherwise the catalog's code default wins.

CREATE TABLE IF NOT EXISTS paper_strategy_status (
    strategy_id     TEXT        PRIMARY KEY,
    status          TEXT        NOT NULL
                    CHECK (status IN ('evaluating', 'backtest-ok', 'scheduled', 'live-eligible')),
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      TEXT        NOT NULL DEFAULT 'system'
);
