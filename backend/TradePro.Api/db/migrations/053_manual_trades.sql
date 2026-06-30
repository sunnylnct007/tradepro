-- 053_manual_trades.sql
--
-- Manually-booked paper trades that originate from the USER's own analysis
-- (the Ichimoku/range-risk checklist, the Today's Setups scanner) — NOT from
-- the signal engine. Kept in their OWN table, completely separate from the
-- engine's oms_orders flow, so there's zero session conflict and two
-- independent P&L tracks. This is Pillar #3: the decision→outcome journal that
-- scores the user's own discretionary calls over time.
--
-- qty is NUMERIC (fractional shares allowed). PnL is computed on close
-- (long: (exit-entry)*qty; short: (entry-exit)*qty).

CREATE TABLE IF NOT EXISTS manual_trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL DEFAULT 'BUY',          -- BUY (long) | SELL (short)
    qty             NUMERIC(18,6) NOT NULL,
    entry_price     NUMERIC(18,6),
    entry_time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stop_loss       NUMERIC(18,6),
    take_profit     NUMERIC(18,6),
    reason          TEXT,                                 -- why: the checklist rationale
    source          TEXT NOT NULL DEFAULT 'manual',       -- manual | manual_checklist | scanner | mcp
    checklist_score TEXT,                                 -- e.g. "5/6"
    currency        TEXT NOT NULL DEFAULT 'USD',
    status          TEXT NOT NULL DEFAULT 'OPEN',         -- OPEN | CLOSED
    exit_price      NUMERIC(18,6),
    exit_time       TIMESTAMPTZ,
    exit_reason     TEXT,                                 -- target | stop | manual | signal_flip
    pnl_usd         NUMERIC(18,6),
    pnl_pct         NUMERIC(12,4),
    created_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_manual_trades_status ON manual_trades(status, entry_time DESC);
