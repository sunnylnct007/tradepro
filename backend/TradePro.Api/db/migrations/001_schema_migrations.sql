-- 001_schema_migrations.sql
-- Bootstrap the migrations tracker table itself. Idempotent — running
-- this twice is safe. Subsequent migrations only run if their filename
-- (e.g. "002_core_stores") isn't already in this table.

CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT        PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT        NOT NULL
);

COMMENT ON TABLE schema_migrations IS
    'Tracks which migration files have been applied. The runner skips any file whose stem is already present here.';
COMMENT ON COLUMN schema_migrations.name IS
    'Migration filename without the .sql extension (e.g. "002_core_stores").';
COMMENT ON COLUMN schema_migrations.checksum IS
    'SHA-256 of the migration body at apply time. A mismatch on a subsequent run means the migration was modified in place — investigate before continuing.';
