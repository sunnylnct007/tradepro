-- 081_desk_check_results.sql
--
-- The desk-check verdict, ON THE SCREEN instead of only in a terminal.
--
-- Owner, 20 Sep 2026: "this is so frustrating. we shd be highlighting that on
-- our dashboard if we are not able to action certian things so we can fix it.
-- observability and diagnostic is key."
--
-- He is right and the evidence is blunt. `tradepro-desk-check` already computes
-- the right thing — one verdict per lane, loud on failure. It could only print
-- and mail, so it existed only if a human ran it. The first time anyone did, it
-- reported:
--
--   [FAIL] mean_reversion_swing_ibkr: 91 orders · 12 reached the broker ·
--          12 filled · exits 0/47 — it can OPEN positions and has never CLOSED one
--
-- Twelve positions open, forty-seven exit attempts, zero closes, and nothing on
-- any screen said so. A check whose output nobody sees is not observability.
--
-- ONE ROW, label='latest'. Same minimal pattern as signal_audit_results and
-- today_setups_results — deliberately, so there is one shape for "a CLI emitted
-- a blob and the desk renders it" rather than a third invention.

CREATE TABLE IF NOT EXISTS desk_check_results (
    label           TEXT PRIMARY KEY DEFAULT 'latest',
    -- {verdict, checks:[{lane,status,detail,fix}], counts:{...}} — the CLI's
    -- emit shape, opaque here on purpose: the renderer must not need a
    -- migration every time a lane is added.
    artifact        JSONB NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    uploaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     TEXT,
    note            TEXT
);

COMMENT ON TABLE desk_check_results IS
  'Latest desk-check verdict. Read by the cockpit banner so a BROKEN lane is visible without anyone running a CLI or reading a mail.';
