-- 062_earnings_calendar.sql
-- Central earnings-date store (design decided with owner, 10 Aug 2026).
--
-- WHY THIS EXISTS: the earnings-proximity gate needs a report date per symbol,
-- but the per-symbol Finnhub calls rate-limit at universe scale (182/728 names
-- came back EARNINGS_UNKNOWN in one run) and IBKR's free snapshot serves no
-- earnings-date field (verified 10 Aug: 60-field sweep on NVDA, known Aug-26
-- date, nothing date-like — WSH is a paid add-on). So report dates live HERE,
-- filled two ways: (1) a nightly Finnhub BULK harvest — ONE
-- /calendar/earnings?from&to call covers the whole market, no per-symbol
-- fan-out; (2) the owner's manual uploads via tradepro-earnings-upload
-- ("I should be able to download earnings data and upload easily").
-- The gate consults this table FIRST; live per-symbol calls are fallback only.
-- One row per (symbol, report_date); re-runs upsert. Additive + idempotent.

CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol       TEXT NOT NULL,
    report_date  DATE NOT NULL,
    session      TEXT,                        -- 'bmo' / 'amc' / '' — before/after market; NULL = unknown
    source       TEXT NOT NULL DEFAULT 'finnhub_bulk',
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, report_date)
);

-- The gate's read path is "dates near today for one symbol"; the harvest's
-- write path replaces a date window — both want report_date ordered.
CREATE INDEX IF NOT EXISTS idx_earnings_calendar_date
    ON earnings_calendar (report_date);
