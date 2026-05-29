-- 026_strategy_broker_map_ichimoku_to_t212.sql
--
-- Correct the broker for `ichimoku_equity`. The 021 seed pointed it
-- at IG_DEMO when in production the equity sleeve has always traded
-- through T212 (the IG demo on equities was aspirational then; the
-- T212 demo is the one that's actually been validated with real
-- fills). Confirmed by the operator on 2026-05-29.
--
-- intraday_flat (added in 024) stays on IG_DEMO — that strategy was
-- designed end-to-end for IG.
--
-- Why UPDATE not INSERT ... ON CONFLICT DO NOTHING:
--   This is a CORRECTION, not a fresh seed. If the row exists (from
--   021) we change it. If the row doesn't exist (an operator removed
--   it via the UI), we leave it alone so the strategy uses the global
--   default — a deleted row is an explicit operator decision and a
--   migration shouldn't override that.
--
-- Operator note: this is the durable fix. The new Settings-page
-- broker-mapping editor (PR #31) lets a trader flip this without a
-- migration; a re-flip via UI will stick until the next migration
-- explicitly overrides it.

UPDATE strategy_broker_map
SET broker         = 'T212_DEMO',
    note           = 'US equity sleeve via T212 demo (corrected from 021 seed)',
    updated_at_utc = NOW(),
    updated_by     = 'migration_026'
WHERE strategy_id = 'ichimoku_equity'
  AND broker = 'IG_DEMO';   -- only flip the legacy seed value, not a deliberate operator choice that already moved it elsewhere
