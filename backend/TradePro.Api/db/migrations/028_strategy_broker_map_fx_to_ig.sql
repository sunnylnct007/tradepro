-- 028_strategy_broker_map_fx_to_ig.sql
--
-- Force ichimoku_fx_mr → IG_DEMO. FX/CFD has no Trading 212 public API
-- (T212 is Invest-only: equities + ETFs), so FX orders routed to T212
-- can NEVER fill — they sit PENDING or get rejected, and the cockpit
-- flags them "T212 ✗ FX". A manual trigger that picked the default
-- broker (T212) mis-routed the whole FX sleeve there. FX belongs on IG.
--
-- UPDATE (not INSERT … DO NOTHING) because this is a CORRECTION: whatever
-- the row currently says (T212_*, or a UI re-flip), FX must be on IG. If
-- the row was deleted, re-create it so the strategy doesn't fall back to
-- the equity default_broker (T212).
--
-- The RiskGate also now hard-rejects FX-on-T212 as a runtime backstop
-- (broker_capability gate), so this map fix + that gate are belt-and-braces.

INSERT INTO strategy_broker_map (strategy_id, broker, note, updated_by)
VALUES ('ichimoku_fx_mr', 'IG_DEMO',
        'G10 FX intraday via IG demo — T212 has no FX API', 'migration_028')
ON CONFLICT (strategy_id) DO UPDATE
    SET broker         = 'IG_DEMO',
        note           = EXCLUDED.note,
        updated_at_utc = NOW(),
        updated_by     = 'migration_028';
