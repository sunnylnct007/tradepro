-- 035_paper_pnl_points.sql
-- Append-only per-strategy P&L points for the cockpit "P&L at a glance"
-- graph. paper_sessions is UPSERTed by session_label ("{strategy}-{date}")
-- so it keeps only the LATEST snapshot per strategy per day — great for the
-- ALL-TIME DAILY curve (one row per strategy per day) but it loses the
-- intraday shape. This table captures one row PER PUSH (every 5-15 min)
-- so the TODAY-INTRADAY curve has real points through the session.
--
-- Daily curve  → read paper_sessions (history already exists).
-- Intraday curve → read paper_pnl_points for today (populates going forward).

CREATE TABLE IF NOT EXISTS paper_pnl_points (
    id              BIGSERIAL   PRIMARY KEY,
    strategy_id     TEXT        NOT NULL,
    broker          TEXT        NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    realised_pnl    DOUBLE PRECISION NOT NULL DEFAULT 0,
    unrealised_pnl  DOUBLE PRECISION NOT NULL DEFAULT 0,
    equity          DOUBLE PRECISION NOT NULL DEFAULT 0,
    received_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The intraday query filters by strategy + time window; the daily rollup
-- (if ever served from here) groups by strategy + day.
CREATE INDEX IF NOT EXISTS paper_pnl_points_strat_ts
    ON paper_pnl_points (strategy_id, as_of_utc DESC);

COMMENT ON TABLE paper_pnl_points IS
    'Append-only per-strategy P&L sample, one row per --push snapshot. '
    'Feeds the cockpit intraday P&L-at-a-glance curve. Daily curve reads '
    'paper_sessions instead. Safe to prune rows older than ~90 days.';
