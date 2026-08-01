-- 059_verdicts.sql
-- Verdict store (TRADEPRO_REDESIGN_SPEC.md §5) — the system of record for every
-- swing/wheel/invest call Symbol Lab (or any future caller) makes, and how it
-- actually scored. Per the spec's storage policy (§9.1): Postgres is the ONLY
-- durable tier for this; ~/sourcecode/tradepro/verdicts.jsonl is a one-time seed
-- to import then delete, not a pattern to repeat.
-- Additive + idempotent so it can't disturb live data.

CREATE TABLE IF NOT EXISTS verdicts (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INT NOT NULL DEFAULT 1,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol       TEXT NOT NULL,
    spot         NUMERIC NOT NULL,
    horizon      TEXT NOT NULL CHECK (horizon IN ('swing', 'wheel', 'invest')),
    verdict      TEXT NOT NULL CHECK (verdict IN ('YES', 'NO', 'NOT_YET', 'WAIT')),
    spec         JSONB,
    why          TEXT NOT NULL,
    -- Mandatory: no verdict without the argument against it (spec §5.1 comment).
    counter_case TEXT NOT NULL,
    evidence     JSONB NOT NULL,
    source       TEXT NOT NULL DEFAULT 'symbol_lab'
);

CREATE INDEX IF NOT EXISTS ix_verdicts_symbol_ts ON verdicts (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS ix_verdicts_horizon_ts ON verdicts (horizon, ts DESC);

-- Spec §5.1 wrote PRIMARY KEY (verdict_id, horizon_days) with horizon_days
-- nullable ("NULL for wheel") — Postgres (and ANSI SQL) rejects NULL in a PK
-- column outright, so that literal DDL can't be created. Same one-row-per-
-- (verdict, horizon) intent, preserved via a surrogate id + a unique index
-- on COALESCE(horizon_days, -1) so wheel rows (horizon_days IS NULL) still
-- collapse to one outcome per verdict instead of allowing duplicates.
CREATE TABLE IF NOT EXISTS verdict_outcomes (
    id           BIGSERIAL PRIMARY KEY,
    verdict_id   BIGINT NOT NULL REFERENCES verdicts (id),
    -- 20/60 for swing+invest; NULL for wheel (scored at option expiry instead).
    horizon_days INT,
    scored_at    TIMESTAMPTZ,
    ref_price    NUMERIC,
    return_pct   NUMERIC,
    outcome      TEXT CHECK (
        outcome IS NULL OR outcome IN ('WIN', 'LOSS', 'EXPIRED_WORTHLESS', 'ASSIGNED', 'PENDING')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_verdict_outcomes_verdict_horizon
    ON verdict_outcomes (verdict_id, COALESCE(horizon_days, -1));
