-- 038_catalysts_registry.sql
--
-- Catalyst overlay — Phase C-1 (registry + visibility).
--
-- Persists dated catalysts (events with a specific timeframe that
-- could move a stock): elections, earnings, FOMC meetings, FDA
-- decisions, M&A close dates, ex-dividends, etc. The Python
-- `tradepro_strategies.catalysts` extractor already produces these
-- from news headlines but has nowhere to write them; this table is
-- where they (and operator-supplied catalysts) land.
--
-- North-star motivation: the trader memory flags catalyst overlay as
-- #1 missing gap. Pure-technical signals miss event-driven trades
-- (Ecopetrol 2026-05-21: tech said WAIT, real trade was BUY because
-- of Colombia election + oil at $105). Phase C-1 is the persistence
-- + visibility layer. C-2 wires the existing extractor sink to POST
-- the rows it finds. C-3 surfaces catalysts as a signal-side overlay
-- in the cockpit decision row.
--
-- Idempotency: (symbol, kind, occurs_on, source) is unique. Re-
-- running the extractor on the same news bundle never duplicates a
-- row. Manual operator additions use source='operator' so they don't
-- collide with the extractor's source identifiers ('news.yahoo',
-- 'gdelt', 'finnhub_earnings_calendar', etc.).

CREATE TABLE IF NOT EXISTS catalysts (
    id              BIGSERIAL   PRIMARY KEY,
    symbol          TEXT        NOT NULL,
    kind            TEXT        NOT NULL,
        -- 'election', 'earnings', 'central_bank', 'commodity',
        -- 'regulatory', 'm&a', 'ex_dividend', 'operator_note', ...
        -- Open list; CHECK is deferred so a new extractor can land
        -- a new kind without a migration. The UI maps unknown kinds
        -- to a neutral colour + a "?" icon.
    occurs_on       DATE,
        -- The catalyst's own date. NULL when the extractor knows
        -- about the event but can't pin a date (e.g. "FDA decision
        -- expected this quarter"). The cockpit panel still shows it
        -- but sorts unscheduled rows below scheduled ones.
    title           TEXT        NOT NULL,
        -- Free-text headline / description. Operator-friendly so it
        -- can be read at a glance.
    source          TEXT        NOT NULL,
        -- Where this catalyst came from. Examples:
        --   'extractor.news'              — Python extractor over news
        --   'gdelt'                       — GDELT geopolitical feed
        --   'finnhub_earnings_calendar'   — Finnhub earnings calendar
        --   'operator'                    — manual cockpit entry
    severity        TEXT        NOT NULL DEFAULT 'medium',
        -- 'low' / 'medium' / 'high'. UI colour-codes; operator can
        -- override per row. High = "this is the entire reason a
        -- trade exists" (Ecopetrol election).
    status          TEXT        NOT NULL DEFAULT 'active',
        -- 'active' / 'expired' / 'dismissed'.
        -- A row becomes 'expired' a few days after `occurs_on`
        -- passes; 'dismissed' is operator action ("noise, ignore").
    surfaced_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
        -- Free-form: news URL, related symbols, confidence score, etc.
    note            TEXT,
        -- Operator commentary ("Bessent gave NDR signal at 2pm").
    created_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, kind, occurs_on, source)
);

CREATE INDEX IF NOT EXISTS catalysts_symbol_occurs
    ON catalysts (symbol, occurs_on DESC);

-- "Show me everything happening in the next 7 days" — the cockpit
-- panel's main query, scoped by occurs_on + status.
CREATE INDEX IF NOT EXISTS catalysts_active_window
    ON catalysts (occurs_on, status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS catalysts_severity
    ON catalysts (severity, occurs_on)
    WHERE status = 'active';

COMMENT ON TABLE catalysts IS
    'Dated catalysts (events) that could move a stock. Phase C-1 of the catalyst overlay (north-star item). Operator + extractor both write; cockpit reads.';

COMMENT ON COLUMN catalysts.kind IS
    'Open enum: election, earnings, central_bank, commodity, regulatory, m&a, ex_dividend, operator_note, etc. UI maps unknown kinds to a neutral icon.';

COMMENT ON COLUMN catalysts.severity IS
    'low / medium / high. High = "this is the entire reason a trade exists" (Ecopetrol Colombia election example).';
