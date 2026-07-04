-- 056_run_log.sql
--
-- CENTRAL RUN-LOG — one table every process on every machine writes to, so
-- operational issues are HIGHLIGHTED in one place instead of scattered across
-- per-machine /tmp logs (user rule 2026-07-04). Processes run on the Mac daemons
-- (strategies, harvest, audits), the EC2 API, and ad-hoc tools — a silent failure
-- on any of them (the stale container, the disabled secret, a swallowed price
-- fetch) should show up here, loud.
--
-- Append-only (many rows); the cockpit reads the recent slice + highlights
-- failures/stale. kind covers ALL operations (trade / price_load / harvest /
-- api_call / session / other); broker covers ALL integrations (IBKR/IG/T212).

CREATE TABLE IF NOT EXISTS run_log (
    id              BIGSERIAL PRIMARY KEY,
    process         TEXT NOT NULL,              -- 'bar-cache-harvest', 'paper-equity', 'signal-audit', ...
    machine         TEXT,                       -- hostname the process ran on
    kind            TEXT NOT NULL,              -- trade | price_load | harvest | api_call | session | other
    broker          TEXT,                       -- IBKR | IG | T212 | null
    symbol          TEXT,                       -- optional (per-symbol events)
    status          TEXT NOT NULL,              -- ok | fail | partial | stale | warn
    error           TEXT,                       -- reason on fail/warn (fail-loud — never null on fail)
    summary         TEXT,                       -- short human line
    started_at_utc  TIMESTAMPTZ,
    finished_at_utc TIMESTAMPTZ,
    created_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_run_log_recent ON run_log(created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_run_log_status ON run_log(status, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_run_log_process ON run_log(process, created_at_utc DESC);
