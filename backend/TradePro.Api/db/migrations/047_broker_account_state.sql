-- 047_broker_account_state.sql
-- Latest pushed ACCOUNT STATE (NLV + cash + positions with live marks) for
-- brokers the server can't reach directly. IBKR PAPER (DUP656969) is the case:
-- the .NET IBKR client is bound to the LIVE harvesting account, so the algo
-- daemon (connected to the paper Gateway) pushes the paper portfolio here, and
-- the cockpit serves it (account P&L row + per-position marks/P&L).
CREATE TABLE IF NOT EXISTS broker_account_state (
    broker          TEXT PRIMARY KEY,        -- e.g. IBKR_PAPER
    account_id      TEXT,
    currency        TEXT,
    net_liquidation NUMERIC,
    total_cash      NUMERIC,
    unrealised_pnl  NUMERIC,
    daily_pnl       NUMERIC,
    positions       JSONB,                   -- [{symbol,qty,mark,marketValue,avgCost,unrealisedPnl}]
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
