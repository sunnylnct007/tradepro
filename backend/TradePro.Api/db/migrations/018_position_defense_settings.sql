-- 018_position_defense_settings.sql
--
-- Seeds the per-position defensive rules for the RiskMonitorService:
-- stop-loss + take-profit. These run as DEFENSIVE OVERLAYS on the
-- trader's daily algo — they don't change when we open positions
-- (the slow loop is still the only opener), but they DO let us cap
-- losses + lock in big wins intraday.
--
-- Per project_overnight_risk_options: option (A) defensive overlay,
-- not option (B) flat-EOD. Preserves trend-following economics while
-- still defending against gap-downs / runaway losers / news shocks.

INSERT INTO app_settings_kv
    (key, value, value_type, label, description, category, min_value, max_value)
VALUES
    ('risk_monitor_max_drawdown',
     '0.10'::jsonb, 'number',
     'Portfolio drawdown trigger (fraction)',
     'When portfolio value drops below this fraction of its rolling '
     || 'high-water mark, RiskMonitorService auto-freezes the system. '
     || '0 disables. Default 0.10 = 10% drawdown.',
     'Risk', 0, 0.50),
    ('risk_monitor_min_free_cash_usd',
     '-100'::jsonb, 'number',
     'Min T212 free cash (USD)',
     'When T212 free cash drops below this, auto-freeze. Catches '
     || 'unaccounted commissions / FX conversions / dividend reversals. '
     || 'Default -100 = freeze only if cash goes meaningfully negative.',
     'Risk', -10000, 10000),
    ('risk_monitor_stop_loss_pct',
     '0.03'::jsonb, 'number',
     'Per-position stop-loss (fraction)',
     'When a held position drops below this fraction from its avg '
     || 'entry price, RiskMonitorService logs a position_stop_loss '
     || 'risk_event. 0 disables. Default 0.03 = -3% per position. '
     || 'Operator acts on the alert manually for now; auto-exit '
     || 'wiring comes in the next iteration.',
     'Risk', 0, 0.20),
    ('risk_monitor_take_profit_pct',
     '0.08'::jsonb, 'number',
     'Per-position take-profit (fraction)',
     'When a held position rises above this fraction from its avg '
     || 'entry price, RiskMonitorService logs a position_take_profit '
     || 'risk_event (operator trims manually). 0 disables. Default '
     || '0.08 = +8% per position.',
     'Risk', 0, 0.50)
ON CONFLICT (key) DO NOTHING;
