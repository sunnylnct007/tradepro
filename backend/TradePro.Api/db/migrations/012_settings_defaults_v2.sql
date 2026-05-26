-- 012_settings_defaults_v2.sql
--
-- One-shot: bump the strategy_optimisation_frequency_minutes default
-- from 15 → 240 (4h) for existing installs. Migration 011 changed
-- the INSERT seed for fresh installs, but ON CONFLICT DO NOTHING
-- meant existing rows didn't change. We only force the new default
-- when the row still holds the old default (15) — operator edits to
-- a different value survive untouched.

UPDATE app_settings_kv
SET value = '240'::jsonb,
    updated_at_utc = NOW(),
    updated_by = 'migration_012'
WHERE key = 'strategy_optimisation_frequency_minutes'
  AND value::text = '15';
