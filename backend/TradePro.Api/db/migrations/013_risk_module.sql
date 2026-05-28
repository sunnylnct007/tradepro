-- 013_risk_module.sql
--
-- Risk Module Phase 1 — pre-trade gate schema. See ROADMAP.md
-- "Risk module" section for the full plan; this migration ships
-- only the schema (tables + seeded settings). The .NET RiskService
-- + /risk page land in follow-up slices.
--
-- Three new tables:
--   1. risk_events           — audit trail of every gate decision
--                              (block / size-adjust / kill-switch).
--   2. symbol_blacklist      — operator-curated list of tickers
--                              the gate refuses to place against.
--   3. risk_velocity_window  — short-window order velocity tracker
--                              (rolling per-strategy minute bucket)
--                              used by the velocity gate so we
--                              don't have to re-scan oms_orders on
--                              every request.
--
-- Settings live in app_settings_kv (added by this migration too)
-- so the trader tunes caps from /settings without a redeploy.

-- ── Risk events audit ─────────────────────────────────────────
-- Every risk decision the gate makes against an OrderIntent gets
-- one row here, regardless of outcome. Lets the trader / IT analyst
-- audit "why wasn't this order placed?" without re-running anything.
CREATE TABLE IF NOT EXISTS risk_events (
    id              BIGSERIAL PRIMARY KEY,
    -- The OMS order_id this event belongs to (null when the gate
    -- blocked BEFORE the OMS row was created — block-on-intent).
    order_id        UUID,
    -- Echo of the intent fields so we can audit even if the OMS row
    -- never existed.
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             NUMERIC(20, 8) NOT NULL,
    broker          TEXT NOT NULL,
    -- 'BLOCKED' | 'ALLOWED' | 'SIZE_ADJUSTED' | 'KILL_SWITCH'.
    decision        TEXT NOT NULL CHECK (decision IN
        ('ALLOWED', 'BLOCKED', 'SIZE_ADJUSTED', 'KILL_SWITCH')),
    -- Which gate fired: 'order_size_cap' | 'order_velocity' |
    -- 'cash_check' | 'blacklist' | 'daily_loss_limit' | etc.
    gate            TEXT NOT NULL,
    -- Human-readable reason surfaced in the /risk UI.
    reason          TEXT NOT NULL,
    -- Optional structured detail (e.g. {"limit": 500, "requested": 600}).
    detail_json     JSONB,
    occurred_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_events_occurred
    ON risk_events(occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_strategy
    ON risk_events(strategy_id, occurred_at_utc DESC);

-- ── Symbol blacklist ──────────────────────────────────────────
-- Operator-curated list of tickers the risk gate will never place
-- against. Reasons: illiquid, restricted, broker-incompatible, etc.
-- Acts on the natural / Wikipedia ticker — broker translation
-- happens downstream so we block once + everywhere.
CREATE TABLE IF NOT EXISTS symbol_blacklist (
    ticker          TEXT PRIMARY KEY,
    reason          TEXT,
    added_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    added_by        TEXT NOT NULL DEFAULT 'unknown'
);

-- ── Order velocity tracker ────────────────────────────────────
-- Per-strategy, per-minute counter the gate uses to enforce a
-- max-N-orders-per-minute limit. Rolling window: we keep one row
-- per (strategy, minute-bucket) and the gate sums the last N
-- minutes. Tail-prune on insert keeps the table small.
CREATE TABLE IF NOT EXISTS risk_velocity_window (
    strategy_id     TEXT NOT NULL,
    minute_bucket   TIMESTAMPTZ NOT NULL,   -- date_trunc('minute', now())
    order_count     INT NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_id, minute_bucket)
);

CREATE INDEX IF NOT EXISTS idx_risk_velocity_bucket
    ON risk_velocity_window(minute_bucket);

-- ── Seed Risk settings into app_settings_kv ───────────────────
-- All caps conservative by default — the trader loosens via
-- /settings as they build trust. ON CONFLICT DO NOTHING so this
-- never overwrites operator edits.
INSERT INTO app_settings_kv
    (key, value, value_type, label, description, category, min_value, max_value)
VALUES
    ('risk_max_order_qty',
     '500'::jsonb, 'number',
     'Max order quantity (shares / units)',
     'Hard cap on per-order quantity. Orders exceeding this are '
     || 'BLOCKED with reason "order_size_cap". 0 to disable the gate.',
     'Risk', 0, 100000),
    ('risk_max_order_notional_usd',
     '50000'::jsonb, 'number',
     'Max order notional (USD)',
     'Hard cap on per-order notional (qty × last close). Orders '
     || 'exceeding this are BLOCKED with reason "order_size_cap".'
     || ' 0 to disable.',
     'Risk', 0, 1000000),
    ('risk_max_orders_per_minute',
     '10'::jsonb, 'number',
     'Max orders per strategy per minute (velocity gate)',
     'Anti-runaway: a buggy strategy that re-fires the same order '
     || 'every tick gets stopped after this many in 60s. 0 to disable.',
     'Risk', 0, 1000),
    ('risk_cash_safety_margin',
     '0.90'::jsonb, 'number',
     'Cash safety margin (fraction of free balance)',
     'Order cost must be ≤ T212_free × this fraction. 0.9 leaves a '
     || '10% buffer for fees / slippage / pending fills. Set 1.0 to '
     || 'disable the cash check, 0 to block all orders.',
     'Risk', 0, 1.0),
    ('risk_fail_closed',
     'true'::jsonb, 'bool',
     'Fail-closed when a risk check errors',
     'When TRUE (default), a gate that can''t evaluate (DB down, '
     || 'T212 unreachable, etc.) BLOCKS the order. Set FALSE to '
     || 'allow orders through on gate-evaluation errors — only safe '
     || 'when you trust upstream.',
     'Risk', NULL, NULL)
ON CONFLICT (key) DO NOTHING;
