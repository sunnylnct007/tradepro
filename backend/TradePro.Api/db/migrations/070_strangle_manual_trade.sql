-- REAL fills from manually-placed strangles. Not decisions — executions.
--
-- Owner, 31 Aug 2026: "it got closed but lets record these so we can learn from
-- it", and earlier, on why this matters at all: "we need to start storing these
-- execution data as no platform will provide these for free".
--
-- WHY THIS IS SEPARATE FROM strangle_decision_log. That table records what the
-- SYSTEM decided. This records what the OWNER actually did, and today proved
-- they are not the same thing: the email said 56,600 / 58,200 and the trades
-- placed were 56,900 / 57,900 and 56,900 / 58,000, in two different accounts,
-- at different sizes, one MIS and one NRML. A trade can exist with no matching
-- decision, and a decision usually has no trade. Forcing them into one table
-- would make both unreadable.
--
-- WHY IT IS THE MOST VALUABLE TABLE IN THIS PROJECT. Every published figure for
-- this strategy is Black-Scholes off a volatility index — no skew, no bid-ask,
-- no evidence anyone would be filled there. Today the model said roughly
-- -12,000 on 150 lots while the real position made +396 on 30. These rows are
-- the only honest prices we will ever have, and they cannot be backfilled.

CREATE TABLE IF NOT EXISTS strangle_manual_trade (
    id             BIGSERIAL PRIMARY KEY,
    market         TEXT        NOT NULL,      -- BANKNIFTY / NIFTY / SPY ...
    account        TEXT,                      -- broker account, so two books stay separate
    product        TEXT,                      -- MIS (intraday) | NRML (carried)
    entry_date     DATE        NOT NULL,
    exit_date      DATE,                      -- NULL while still open

    expiry         DATE,
    lots           INT         NOT NULL,
    lot_size       INT,

    put_strike     NUMERIC(18,6),
    put_entry      NUMERIC(18,6),             -- REAL fill, not a model price
    put_exit       NUMERIC(18,6),
    call_strike    NUMERIC(18,6),
    call_entry     NUMERIC(18,6),
    call_exit      NUMERIC(18,6),

    -- Index level at entry and exit — lets a later study ask "what move did this
    -- survive?" without re-sourcing bars and hoping they match.
    index_entry    NUMERIC(18,6),
    index_exit     NUMERIC(18,6),

    realised_pnl   NUMERIC(18,2),             -- as the broker reported it
    currency       TEXT        NOT NULL DEFAULT 'INR',

    -- Did this follow the emailed signal, or was it the owner's own call? The
    -- single most useful column for learning: it separates "the strategy did
    -- this" from "I did this", and today they differed on every leg.
    followed_signal BOOLEAN,
    signal_put_strike  NUMERIC(18,6),
    signal_call_strike NUMERIC(18,6),

    notes          TEXT,
    recorded_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS strangle_manual_trade_market_date
    ON strangle_manual_trade (market, entry_date DESC);
CREATE INDEX IF NOT EXISTS strangle_manual_trade_open
    ON strangle_manual_trade (entry_date DESC) WHERE exit_date IS NULL;

COMMENT ON TABLE strangle_manual_trade IS
    'Real fills from manually-placed index strangles. The only source of true option prices this project has — every backtest figure is Black-Scholes off a vol index, with no skew and no bid-ask. Cannot be backfilled.';
COMMENT ON COLUMN strangle_manual_trade.followed_signal IS
    'TRUE when the placed strikes matched the emailed ones. On 31 Aug 2026 they did not, in either account — which is exactly the comparison worth accumulating.';
