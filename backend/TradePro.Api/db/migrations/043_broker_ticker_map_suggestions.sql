-- Review queue for LOW-CONFIDENCE (name-matched) instrument mappings.
--
-- broker_ticker_map is read by live order routing (PostgresOmsService.
-- ResolveBrokerSymbolAsync). Exact-ticker and ISIN matches are trustworthy;
-- NAME matches are not — distinct issuers can share a name (e.g. the rebuild
-- produced "MZTI→LANC_US_EQ", two different companies). A wrong mapping
-- mis-routes a REAL order to the wrong instrument, which is worse than no
-- mapping. So name matches land here for a human to promote, and NEVER go
-- straight into the live routing map.
CREATE TABLE IF NOT EXISTS broker_ticker_map_suggestions (
    broker                   TEXT NOT NULL,
    source_ticker            TEXT NOT NULL,
    suggested_broker_ticker  TEXT NOT NULL,
    method                   TEXT NOT NULL,
    isin                     TEXT,
    created_at_utc           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broker, source_ticker)
);

-- One-time cleanup: remove any name-matched rows a prior rebuild wrote into the
-- LIVE routing map before this safety rail existed. They get re-derived into
-- the suggestions queue on the next rebuild. Exact/ISIN rows are untouched.
DELETE FROM broker_ticker_map WHERE note IN ('byname', 'name');
