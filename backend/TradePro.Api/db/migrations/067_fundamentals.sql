-- FUNDAMENTALS store — P/E, ROE, margins and the reported EPS trend.
--
-- Owner, 29 Aug 2026: "for fundamental we just need latest data", "if i have to
-- decide for trades for next week I will look at the fundamentals of that
-- company", and "in fact this figure shd be visible on our data harvesting
-- screen as well".
--
-- The figures were harvested to a JSON file on one laptop. The desk could not
-- see them at all, so the one screen that lists every symbol we hold data for
-- could not say whether a name was profitable.
--
-- SNAPSHOT, not history. as_of records when it was fetched. This table answers
-- "what is true now" for a human deciding a trade; it must NEVER be used to
-- backtest a past date -- stamping today's P/E on a 2023 event is look-ahead and
-- would manufacture an edge out of nothing. Point-in-time work reads
-- annual_eps, which carries its own fiscal-period keys.

CREATE TABLE IF NOT EXISTS fundamentals (
    symbol            text PRIMARY KEY,
    as_of             timestamptz NOT NULL DEFAULT now(),
    source            text        NOT NULL DEFAULT 'yfinance',
    trailing_pe       numeric(18,4),
    forward_pe        numeric(18,4),
    price_to_book     numeric(18,4),
    return_on_equity  numeric(18,6),
    profit_margin     numeric(18,6),
    debt_to_equity    numeric(18,4),
    trailing_eps      numeric(18,4),
    forward_eps       numeric(18,4),
    market_cap        numeric(24,2),
    -- {"2025-08-31": 7.59, ...} — fiscal period end -> diluted EPS as REPORTED.
    -- Kept beside the snapshot so the EPS trend renders without a second call,
    -- and so point-in-time work has the dated figures it needs.
    annual_eps        jsonb
);

COMMENT ON TABLE fundamentals IS
  'CURRENT fundamentals per symbol. Decision support for a human, never a backtest input — today''s P/E on a past date is look-ahead.';

CREATE INDEX IF NOT EXISTS idx_fundamentals_as_of ON fundamentals(as_of DESC);
