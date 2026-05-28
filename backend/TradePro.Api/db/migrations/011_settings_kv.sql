-- 011_settings_kv.sql
--
-- Free-form key-value settings table — complements the existing
-- single-row JSONB `settings` table for fields that don't deserve
-- their own AppSettings record property. As the UI grows we'll be
-- adding a lot of operator-tunable knobs (strategy optimisation
-- frequency, default universe, daily-run window, per-strategy
-- rate-limits, etc.) — bake them all as one-line additions here
-- rather than evolving a giant record class every time.
--
-- Why a NEW table rather than extending the existing `settings`:
--   * The existing AppSettings is strongly-typed (record class +
--     JSON serialiser) which is the right shape for fields the
--     backend's compiled code reads directly (sentiment thresholds,
--     intraday gate). New fields there require a code change.
--   * This new table is intentionally schema-free at the API
--     boundary — the value is a JSONB blob keyed by string. New
--     settings are a one-line addition: `UPSERT INTO app_settings_kv
--     (key, value) VALUES ('strategy_optimisation_frequency_minutes',
--     '15'::jsonb)`. The UI fetches { key, value, type, description }
--     so the operator can edit without a code change.
--
-- Both tables coexist; over time the strongly-typed AppSettings
-- record stays small (only fields that hot-path code reads directly)
-- and the bulk of operator-tunable config lives here.

CREATE TABLE IF NOT EXISTS app_settings_kv (
    key                TEXT PRIMARY KEY,
    -- Free-form JSON value. UI parses based on `value_type`.
    value              JSONB NOT NULL,
    -- Hint for the UI's input renderer: 'number', 'string', 'bool',
    -- 'json', 'string_list', 'cron'. Optional — defaults to 'json'
    -- when the API can't infer. The hint NEVER changes how the value
    -- is stored, only how it's edited.
    value_type         TEXT NOT NULL DEFAULT 'json',
    -- Human-readable label + explainer for the UI form. Shown next
    -- to the input so the operator never has to grep docs.
    label              TEXT,
    description        TEXT,
    -- Grouping for the settings page tabs / sections (e.g. 'Trading',
    -- 'Daemon', 'Notifications', 'Risk'). Free-form.
    category           TEXT NOT NULL DEFAULT 'General',
    -- Validation hints (min / max for numbers, enum for strings).
    -- Optional; the UI degrades to a free input when null.
    min_value          DOUBLE PRECISION,
    max_value          DOUBLE PRECISION,
    allowed_values     JSONB,
    -- Audit trail (light — single updater per key).
    updated_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by         TEXT NOT NULL DEFAULT 'unknown'
);

CREATE INDEX IF NOT EXISTS idx_app_settings_kv_category
    ON app_settings_kv(category);

-- Seed a couple of canonical entries the user has already asked for
-- so the new Settings page has live data on day one. ON CONFLICT DO
-- NOTHING means a re-run never blows away operator edits.
INSERT INTO app_settings_kv
    (key, value, value_type, label, description, category, min_value, max_value)
VALUES
    ('strategy_optimisation_frequency_minutes',
     '240'::jsonb, 'number',
     'Strategy optimisation frequency (minutes)',
     'How often the universe-wide optimiser re-evaluates each strategy. '
     || 'Default 240 (4h) — most strategies don''t benefit from sub-hour '
     || 'cadence and we save data-provider load. Lower = fresher signals + '
     || 'more compute. Set to 0 to disable continuous optimisation entirely '
     || '(manual triggers still work). Read by the daemon on its next tick.',
     'Trading', 0, 1440),
    ('daemon_universe_default',
     '"sp500"'::jsonb, 'string',
     'Default universe for the daily ichimoku daemon',
     'Which Wikipedia-scraped universe the auto-trigger uses when no '
     || 'symbols are supplied. Picker on /trader still overrides per run.',
     'Trading', NULL, NULL),
    ('daily_ichimoku_run_utc',
     '"13:25"'::jsonb, 'string',
     'Daily Ichimoku auto-run time (UTC, HH:MM)',
     '5 minutes before US market open by default so the universe '
     || 'refresh + strategy session both complete before MOO entry.',
     'Daemon', NULL, NULL),
    ('top_n_signals_per_run',
     '5'::jsonb, 'number',
     'Top N signals to propose for order placement per scan',
     'When the strategy fires fire-* on >N symbols across the universe, '
     || 'the picker surfaces only the top N (ranked by signal strength) '
     || 'as suggested orders. Rest stay visible in the scan grid for '
     || 'manual review.',
     'Trading', 1, 50)
ON CONFLICT (key) DO NOTHING;
