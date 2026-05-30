-- 031_bar_cache_events.sql
--
-- Per-fetch structured telemetry for the BarStore (Phase B of the
-- trustworthy-data roadmap). Every BarStore.get() call emits one
-- row here — successful cache hits, provider misses, manifest
-- violations, the lot. This is the substrate the cockpit's
-- "is the data layer healthy?" panel reads from in Phase G.
--
-- The Python side writes via best-effort: if the DB is unreachable
-- the event also gets logged to a local JSON file under
-- ~/.tradepro/bar_cache/events/YYYY-MM-DD.jsonl. The DB is the
-- authoritative store; the file is recovery.
--
-- Indexes are tuned for the two anticipated queries:
--   1. "Show me the last N events for symbol X" → (canonical, occurred_at_utc)
--   2. "Show me every fetch in the last hour grouped by result"
--      → (occurred_at_utc) BRIN-style (we use a regular btree for
--      flexibility; Postgres BRIN buys little until > millions of rows)

CREATE TABLE IF NOT EXISTS bar_cache_events (
    id                 BIGSERIAL PRIMARY KEY,
    occurred_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Identity of the fetch
    canonical          TEXT NOT NULL,          -- "SPY" (symbol_map key)
    asset_class        TEXT NOT NULL,          -- "us_etf"
    resolution         TEXT NOT NULL,          -- "1m" / "1d" / etc.
    range_start_utc    TIMESTAMPTZ NOT NULL,
    range_end_utc      TIMESTAMPTZ NOT NULL,

    -- Result classification — what happened on this fetch
    result             TEXT NOT NULL,
        -- "complete"            cache hit, manifest validated, full coverage
        -- "fetched_complete"    cache miss → provider → complete after write
        -- "fetched_partial"     provider returned fewer bars than expected
        --                       (still wrote; manifest marks gaps)
        -- "manifest_violation"  on-disk parquet didn't match manifest
        -- "provider_error"      every provider in the chain failed
        -- "rate_limited"        provider returned 429 (chain may still succeed)
        -- "no_provider"         no provider configured for (asset_class, resolution)

    -- Provider chain — what we tried, what answered
    source_chain       TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                        -- e.g. ['cache_hit'] or ['cache_miss','yfinance_429','ig_prices_ok']
    provider_used      TEXT,                   -- the one that ultimately answered (or NULL)
    provider_versions  JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Volume + validation
    rows_expected      INT,
    rows_returned      INT,
    gaps_detected_count INT NOT NULL DEFAULT 0,
    schema_version     TEXT NOT NULL,          -- "us_equity_v1" etc.

    -- Performance
    latency_ms         INT NOT NULL DEFAULT 0,

    -- Failure detail (NULL on success)
    error_class        TEXT,                   -- "network" | "rate_limit" | "parse" | "manifest" | "schema" | "delisted" | "corporate_action_ambiguous"
    error_provider     TEXT,
    error_message      TEXT,
    retry_strategy     TEXT                    -- "exponential_backoff" | "switch_provider" | "user_intervention" | "fatal"
);

ALTER TABLE bar_cache_events
    DROP CONSTRAINT IF EXISTS bar_cache_events_result_check;
ALTER TABLE bar_cache_events
    ADD CONSTRAINT bar_cache_events_result_check
    CHECK (result IN (
        'complete',
        'fetched_complete',
        'fetched_partial',
        'manifest_violation',
        'provider_error',
        'rate_limited',
        'no_provider'
    ));

CREATE INDEX IF NOT EXISTS bar_cache_events_canonical_occurred
    ON bar_cache_events (canonical, occurred_at_utc DESC);

CREATE INDEX IF NOT EXISTS bar_cache_events_occurred
    ON bar_cache_events (occurred_at_utc DESC);

CREATE INDEX IF NOT EXISTS bar_cache_events_result_occurred
    ON bar_cache_events (result, occurred_at_utc DESC);

COMMENT ON TABLE bar_cache_events IS
    'Per-fetch telemetry from the BarStore. Phase B of the trustworthy-data roadmap. Cockpit reads this for the data-health dashboard.';
