-- 044_ig_l1_snapshots.sql
--
-- Continuous IG L1 capture — the "rely less on Yahoo" data lake
-- foundation. The IGSnapshotHarvester BackgroundService polls
-- /markets/{epic} on a configurable cadence (default 5 min during
-- the IG-tradeable hours) and writes one row per epic per tick to
-- this table.
--
-- Why this exists:
--
--   1. **Lake the trader OWNS.** yfinance can rate-limit, revise
--      historical bars, or disappear (M&A / cert changes). Bars
--      built from IG snapshots we wrote ourselves never revise.
--
--   2. **Replay foundation.** Given a date range, the replay engine
--      can reconstruct what each strategy was looking at to the
--      precision of the harvester cadence — no "what did SPY look
--      like at 14:35 yesterday" guesswork.
--
--   3. **Fill-quality denominator.** Phase F-3 fill-quality already
--      stamps bid/ask on every fill (oms_fills). With this table, we
--      can answer "what was the bid/ask just BEFORE we placed the
--      order" — separating decision-time spread from execution-time
--      spread for honest slippage analysis.
--
-- Schema design notes:
--
--   * One row PER POLL — never collapse to a single "latest" row per
--      symbol because the time series IS the value.
--
--   * Numeric (not double) on bid/ask/mid — FX pip precision (5
--      decimal places on majors) needs the exact rounding.
--
--   * `epic` stored alongside `symbol` because the IG market.status
--      / dealing rules / corp-action history all key off epic, not
--      canonical symbol. Joining back to ig_epic_map.json downstream
--      stays cheap.
--
--   * `market_status` (TRADEABLE / EDITS_ONLY / OFFLINE) captures
--      WHY a bid/ask is missing rather than dropping the row. Lets
--      the replay engine show "market closed" gaps honestly.
--
--   * `source` tagged 'ig_markets' so a future Oanda / IBKR harvester
--      can write into the same table with a different tag, and the
--      cockpit can show "which provider sourced this minute".
--
-- This is **append-only** at this scale. A 1-symbol polled every 5
-- minutes across the IG-tradeable 22 hours/day = ~265 rows/day per
-- symbol. 50 symbols ~ 13k rows/day = ~5M rows/year. The
-- `(symbol, captured_at_utc DESC)` partial index keeps the cockpit's
-- "last N snapshots for SPY" query sub-millisecond at that volume.

CREATE TABLE IF NOT EXISTS ig_l1_snapshots (
    id                BIGSERIAL   PRIMARY KEY,
    symbol            TEXT        NOT NULL,
    epic              TEXT        NOT NULL,
    bid               NUMERIC(12, 5),
    ask               NUMERIC(12, 5),
    mid               NUMERIC(12, 5)
                                    GENERATED ALWAYS AS ((bid + ask) / 2) STORED,
    spread_bps        NUMERIC(10, 2)
                                    GENERATED ALWAYS AS (
                                        CASE
                                            WHEN bid IS NULL OR ask IS NULL OR bid = 0
                                                THEN NULL
                                            ELSE ((ask - bid) / ((bid + ask) / 2)) * 10000
                                        END
                                    ) STORED,
    market_status     TEXT,            -- TRADEABLE / EDITS_ONLY / OFFLINE / null
    update_time       TEXT,            -- IG's own update timestamp string
    captured_at_utc   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source            TEXT        NOT NULL DEFAULT 'ig_markets',
    error             TEXT             -- non-null when bid/ask are null AND we know why
);

-- Latest-N-per-symbol queries — covers the cockpit "snapshot activity"
-- panel and the replay engine's "snapshots between A and B for SPY".
CREATE INDEX IF NOT EXISTS ig_l1_snapshots_symbol_captured
    ON ig_l1_snapshots (symbol, captured_at_utc DESC);

-- All-symbol time-window queries (Phase F-3.1 will join here to
-- estimate empirical slippage from PRE-trade bid/ask).
CREATE INDEX IF NOT EXISTS ig_l1_snapshots_captured
    ON ig_l1_snapshots (captured_at_utc DESC);

-- Health partial index — rows with NO bid/ask are the failures the
-- harvester health panel surfaces. Keeping them separate lets the
-- "% of polls successful" query stay fast even at high row counts.
CREATE INDEX IF NOT EXISTS ig_l1_snapshots_failures
    ON ig_l1_snapshots (symbol, captured_at_utc DESC)
    WHERE bid IS NULL OR ask IS NULL;

COMMENT ON TABLE ig_l1_snapshots IS
    'Continuous IG L1 (bid/ask/mid) capture by IGSnapshotHarvester. The data-lake foundation for trustworthy backtest + replay.';

COMMENT ON COLUMN ig_l1_snapshots.spread_bps IS
    'Computed at insert time so cockpit reads do not pay the divide. Null when bid or ask null.';

COMMENT ON COLUMN ig_l1_snapshots.market_status IS
    'IG-reported market status. Lets gap analysis distinguish "market closed" from "harvester broken".';

COMMENT ON COLUMN ig_l1_snapshots.error IS
    'When a poll succeeded HTTP-wise but bid/ask were missing (delisted, halted, etc.) we record the reason here. Network/auth errors are logged but do NOT produce a row.';
