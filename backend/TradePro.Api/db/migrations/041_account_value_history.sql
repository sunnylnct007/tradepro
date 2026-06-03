-- 041_account_value_history.sql
--
-- Daily snapshot of total account value per broker, so we can draw the
-- equity curve ("is the account growing?") + day-over-day P&L. We only ever
-- read CURRENT broker balances (cash-summary), never stored them — so there's
-- no history to chart. This table is upserted from the cash-summary handler
-- on each poll; ON CONFLICT keeps the LATEST value per (broker, day), so by
-- end of day the row holds that day's closing account value.
--
-- Per-broker raw value + currency (T212 in USD, IG in GBP); the read side
-- converts to a common ccy for the combined curve. We can't backfill history
-- we never captured — the series starts the day this ships.

CREATE TABLE IF NOT EXISTS account_value_history (
    broker          TEXT NOT NULL,
    as_of_date      DATE NOT NULL,
    currency        TEXT,
    total_value     DOUBLE PRECISION NOT NULL,
    captured_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broker, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_account_value_history_date
    ON account_value_history(as_of_date);
