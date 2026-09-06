-- What the strangle WAS QUOTED AT, whether or not we placed it.
--
-- Owner, 6 Sep 2026: "the ones we are not able to fund we can atleast see the
-- potential gain and loss if we would have placed by recording price at market
-- open and at market close".
--
-- NDX resolves perfectly and simply cannot be funded (~$2.85M collateral on a
-- ~$151k account). Today it produces a decision, a strike pair, and nothing
-- else — a market we evaluate every day and learn nothing from.
--
-- These are REAL bid/ask mids from IBKR, not Black-Scholes. That distinction is
-- the whole point: credit_modelled carries no skew and no bid-ask and is not a
-- price anyone was offered, whereas a quoted mid is. It is still NOT a fill —
-- nothing was traded — so it lives in its own columns and must never be summed
-- with credit_actual.
--
-- Worth as much on markets we DO place: quoted-at-entry against credit_actual
-- is a direct measurement of SLIPPAGE, which no backtest can supply.

ALTER TABLE strangle_decision_log
    -- mid x lot x contracts at DECISION time, in money
    ADD COLUMN IF NOT EXISTS quoted_credit   NUMERIC(18,2),
    -- and at the close, so the round trip is priceable
    ADD COLUMN IF NOT EXISTS quoted_exit     NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS quoted_pnl      NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS quoted_at_utc   TIMESTAMPTZ,
    -- Widest leg spread at quote time. A mid is only honest if the market is
    -- tight; a 20-wide book makes the whole number a fiction, and the reader
    -- must be able to SEE that rather than infer it.
    ADD COLUMN IF NOT EXISTS quoted_spread   NUMERIC(18,4);

CREATE INDEX IF NOT EXISTS ix_strangle_decision_quoted
    ON strangle_decision_log (as_of DESC)
    WHERE quoted_credit IS NOT NULL;
