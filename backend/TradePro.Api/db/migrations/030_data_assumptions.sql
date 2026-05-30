-- 030_data_assumptions.sql
--
-- Auditable registry of assumptions TradePro makes about its data
-- and backtest evidence. Every assumption has a status (HONEST /
-- PARTIAL / OPTIMISTIC / FICTIONAL) and a remedy pointing at the
-- roadmap phase that resolves it. The UI panel renders this table
-- so a trader / future investor can read off "what does TradePro
-- pretend is true that isn't?".
--
-- Schema intent: assumptions evolve over time. New ones appear,
-- existing ones get resolved or downgraded. The list is meant to
-- be living — operator can edit (via the admin endpoint) when an
-- assumption changes status, e.g. "we just shipped Phase F so
-- slippage is no longer FICTIONAL, it's OPTIMISTIC".
--
-- Seed reflects the audit captured in CURRENT_BACKTEST_LIMITATIONS.md
-- on the date this migration lands.

CREATE TABLE IF NOT EXISTS data_assumptions (
    id              TEXT PRIMARY KEY,        -- short stable id, e.g. 'L1_intraday_data_ceiling'
    description     TEXT NOT NULL,           -- single sentence the trader reads
    severity        TEXT NOT NULL,           -- CRITICAL | HIGH | MEDIUM | LOW | INFORMATIONAL
    status          TEXT NOT NULL,           -- HONEST | PARTIAL | OPTIMISTIC | FICTIONAL
    affects         TEXT[] NOT NULL,         -- strategy names / surfaces affected
    consequence     TEXT NOT NULL,           -- concrete "what this gets wrong"
    remedy          TEXT NOT NULL,           -- roadmap phase / planned fix
    mitigation      TEXT,                    -- what we do TODAY to manage the risk
    last_reviewed_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_reviewed_by     TEXT NOT NULL DEFAULT 'system'
);

ALTER TABLE data_assumptions
    DROP CONSTRAINT IF EXISTS data_assumptions_severity_check;
ALTER TABLE data_assumptions
    ADD CONSTRAINT data_assumptions_severity_check
    CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFORMATIONAL'));

ALTER TABLE data_assumptions
    DROP CONSTRAINT IF EXISTS data_assumptions_status_check;
ALTER TABLE data_assumptions
    ADD CONSTRAINT data_assumptions_status_check
    CHECK (status IN ('HONEST', 'PARTIAL', 'OPTIMISTIC', 'FICTIONAL'));

-- Seed from CURRENT_BACKTEST_LIMITATIONS.md. Keep ids stable so
-- a future migration that changes a status doesn't lose continuity.
INSERT INTO data_assumptions (
    id, description, severity, status, affects,
    consequence, remedy, mitigation, last_reviewed_by
)
VALUES
    ('L1_intraday_data_ceiling',
     'yfinance 1-minute history is capped at 7 days; no other free provider currently in the chain',
     'CRITICAL', 'FICTIONAL',
     ARRAY['intraday_flat', 'orb', 'vwap_mean_reversion', 'bollinger_bounce', 'ma_crossover'],
     'Intraday strategies cannot be backtested at 1m resolution past 7 days. Cockpit "backtest" of these is a 7-day forward test mislabelled.',
     'Phase B (bar cache + provider chain) + Phase C (backfill UI) + Phase E (refuse incomplete backtests)',
     'Run intraday backtests only over the last 7 days; label results "Forward test (7d)". For longer horizons, use daily-resampled bars and accept the granularity loss.',
     'migration_030'),

    ('L2_slippage_fictional',
     'All backtests fill at OHLC close. No historical bid/ask spread is stored.',
     'HIGH', 'OPTIMISTIC',
     ARRAY['ichimoku_equity', 'ichimoku_fx_mr', 'intraday_flat', 'orb', 'vwap_mean_reversion', 'bollinger_bounce', 'ma_crossover'],
     'Backtest returns are systematically optimistic by the realised spread cost (1bp liquid ETFs, 5-15bp single names, 10-50bp small caps).',
     'Phase F (store IG L1 snapshot at fill; empirical slippage model)',
     'Apply manual haircut when comparing backtest to live: 1bp for ETF, 5bp single-name, more for illiquid. Treat backtest Sharpe within 0.3 of threshold-of-interest skeptically.',
     'migration_030'),

    ('L3_llm_gate_anachronistic',
     'LLM gate fetches CURRENT news at backtest time and applies it to historical entries; no historical sentiment storage exists pre-2026-05-28',
     'HIGH', 'FICTIONAL',
     ARRAY['ichimoku_equity', 'ichimoku_fx_mr', 'intraday_flat'],
     'No correct way to backtest the LLM gate''s contribution today. Enabled = anachronistic. Disabled = live diverges from backtest by an unmeasured amount.',
     'Phase H (replay stored llm_evaluations rows by timestamp — honest from 2026-05-28 onward)',
     'Run backtests with LLMGateConfig.enabled=False. Cockpit must show the flag on every backtest result.',
     'migration_030'),

    ('L4_reproducibility_weak',
     'yfinance silently revises historical bars; no backtest result currently records the data state it used',
     'MEDIUM', 'PARTIAL',
     ARRAY['ichimoku_equity', 'ichimoku_fx_mr', 'all_backtests'],
     'Two runs of the same backtest weeks apart can disagree by a few bps. Walk-forward sweeps have a noise floor higher than zero.',
     'Phase D (stamp every backtest result with data_provider, version, partition hashes; result viewer surfaces the hash)',
     'For a definitive A/B compare, rerun both legs on the same day. Treat backtest "trends over months" with humility.',
     'migration_030'),

    ('L5_no_fill_quality_audit',
     'oms_fills records broker fill price but NOT the bid/ask at fill time, depth ladder, time-to-fill, or slippage vs mid',
     'MEDIUM', 'OPTIMISTIC',
     ARRAY['oms_fills', 'cockpit_audit_panel'],
     'A broker fill at noticeably worse than mid goes undetected. Strategies looking broken in live could be data-layer broken (we filled poorly), not signal-layer broken.',
     'Phase F (store IG L1 snapshot at every fill; per-fill spread / slippage analytics in cockpit)',
     'Spot-check fill samples against the IG demo UI for the same symbol/time, manually compare to mid.',
     'migration_030'),

    ('L6_yfinance_rate_limits_silent',
     'yfinance returns 429 / empty frames on rate-limit; current code treats empty as "no signal" not "data missing"',
     'MEDIUM', 'FICTIONAL',
     ARRAY['all_backtest_sweeps'],
     'Cross-sectional backtest over 100+ symbols at 1m can have a non-trivial fraction come back empty; ranking is biased toward symbols that fetched OK.',
     'Phase A (per-fetch telemetry distinguishes 429 / empty / parse-error / network-error / OK) + Phase B (cache absorbs rate limits)',
     'Run sweeps during off-peak UTC hours. Sanity-check sample symbols against manual yfinance calls after any cross-sectional run.',
     'migration_030'),

    ('L7_dst_holiday_boundaries',
     'Intraday strategy timing windows configured in UTC; DST shifts mean the same UTC time maps to different ET clock times across the year',
     'LOW', 'PARTIAL',
     ARRAY['intraday_flat', 'orb', 'vwap_mean_reversion'],
     'A strategy may enter at a different relative-to-open time across DST boundaries; either tuned for DST and wrong half the year, or vice versa.',
     'Phase B+1 (switch timing windows from UTC to exchange-local time with explicit timezone handling)',
     'Audit each intraday strategy''s timing across a DST boundary.',
     'migration_030'),

    ('L8_asset_class_coverage',
     'Currently sources equity + ETF + FX spot. No infrastructure for options chains, futures, crypto.',
     'INFORMATIONAL', 'HONEST',
     ARRAY['future_strategy_classes'],
     'Strategies needing options or futures data cannot be built today.',
     'Phase B is asset-class-pluggable from day 1; adding a new asset class is a single-file plugin.',
     NULL,
     'migration_030')
ON CONFLICT (id) DO NOTHING;
