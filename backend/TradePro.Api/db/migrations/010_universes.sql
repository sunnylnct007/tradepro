-- 010_universes.sql
--
-- Wikipedia-driven symbol universes. The Mac worker scrapes
-- Wikipedia constituent pages daily (via tradepro-refresh-universes
-- --push) and atomically replaces the rows under each universe name.
-- The .NET API serves them out to the frontend's universe picker.
--
-- Two tables instead of a single JSONB blob so the picker can do
-- sector-level filtering server-side later without re-parsing JSON
-- on every request. Wipe-and-replace per universe (FK CASCADE) is
-- safer than diff'ing constituent changes on every refresh —
-- Wikipedia tables turn over rarely + the worker runs daily so
-- there's no audit-trail loss worth the complexity.

CREATE TABLE IF NOT EXISTS universes (
    name              TEXT PRIMARY KEY,
    source_url        TEXT NOT NULL,
    fetched_at_utc    TIMESTAMPTZ NOT NULL,
    symbol_count      INT NOT NULL,
    -- Free-form so future scrapers (non-Wikipedia: e.g. ETF .com,
    -- the trader's curated CSV) can co-exist under the same table.
    source            TEXT NOT NULL DEFAULT 'wikipedia',
    created_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS universe_symbols (
    universe_name     TEXT NOT NULL REFERENCES universes(name) ON DELETE CASCADE,
    ticker            TEXT NOT NULL,
    name              TEXT,
    sector            TEXT,
    industry          TEXT,
    PRIMARY KEY (universe_name, ticker)
);

CREATE INDEX IF NOT EXISTS idx_universe_symbols_sector
    ON universe_symbols(universe_name, sector);

-- Per-universe override list. Trader can mark specific tickers as
-- INCLUDED (force-add to the picker even if Wikipedia drops them)
-- or EXCLUDED (suppress, e.g. ETFs they don't want to trade, illiquid
-- listings, broker-incompatible tickers). One row per (universe,
-- ticker) — the action column dictates which list it joins.
CREATE TABLE IF NOT EXISTS universe_overrides (
    universe_name     TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    action            TEXT NOT NULL CHECK (action IN ('INCLUDE', 'EXCLUDE')),
    note              TEXT,
    updated_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by        TEXT NOT NULL DEFAULT 'unknown',
    PRIMARY KEY (universe_name, ticker)
);

-- Per-broker ticker translation. Wikipedia's ticker is the "natural"
-- identifier; brokers each speak their own dialect (T212 wants
-- "AAPL_US_EQ" for equities + bare "EURUSD" for FX; IBKR wants
-- "AAPL" + "EUR.USD"). Lookup is (broker, source_ticker) → broker_ticker.
-- Empty / missing rows mean "use the natural ticker as-is" — the
-- existing _to_t212_ticker heuristic remains the fallback.
CREATE TABLE IF NOT EXISTS broker_ticker_map (
    broker            TEXT NOT NULL,         -- 'T212_DEMO' | 'T212_LIVE' | 'IBKR_PAPER' | 'IBKR_LIVE' | 'YAHOO'
    source_ticker     TEXT NOT NULL,         -- the natural / Wikipedia / Yahoo ticker
    broker_ticker     TEXT NOT NULL,         -- the broker's instrument id
    exchange          TEXT,                  -- optional disambiguation when multi-listed
    note              TEXT,
    updated_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broker, source_ticker)
);
