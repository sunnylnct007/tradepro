-- 051_fill_replay.sql
--
-- Stores the latest fill-replay artifact emitted by `tradepro-fill-replay`
-- (replays a strategy's ACTUAL OMS fills → per-symbol entry-extension +
-- outcome + one-shot-vs-staggered, split by universe). This is the honest
-- diagnosis surface for LIVE losses — the backtest models strategy LOGIC,
-- fill-replay measures what we ACTUALLY did (entry timing, concentration).
--
-- Same minimal shape as equity_pipeline_results (014): one row per
-- (strategy, label); artifact is an opaque JSONB blob matching the CLI's
-- emit shape (universes summary + per-symbol rows + missing). Keeping it as
-- a single blob means CLI-side schema evolution doesn't break the API.

CREATE TABLE IF NOT EXISTS fill_replay_results (
    strategy        TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT 'latest',
    artifact        JSONB NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    uploaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     TEXT,
    note            TEXT,
    PRIMARY KEY (strategy, label)
);

CREATE INDEX IF NOT EXISTS idx_fill_replay_results_as_of
    ON fill_replay_results(strategy, as_of_utc DESC);
