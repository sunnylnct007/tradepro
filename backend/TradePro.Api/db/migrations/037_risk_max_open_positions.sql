-- Capital backstop: cap concurrent in-flight BUY orders per strategy.
--
-- Root cause it guards: 2026-06-01 the equity strategy emitted an order for
-- the FULL S&P universe (~500 names) — far more than $50k could fund — and the
-- excess was bulk-cancelled ("stale_preopen_full_sp_universe_too_large_for_capital").
-- The strategy-side top-N-by-conviction selection is the primary fix; this is
-- the OMS-level backstop so a runaway emission (manual flood, bad config,
-- replay bug) is refused at the gate regardless of which path produced it.
--
-- 80 sits comfortably above the equity sleeves' 51 legitimate slots
-- (large_50=20 + high_beta=30 + GLD=1), so it only ever trips on a runaway.
-- Under fixed-slot sizing each position is ~capital/slots, so capping the
-- COUNT caps deployed capital — the closest to a dollar gate the OMS can do
-- without a pre-fill price for market orders.
INSERT INTO app_settings_kv (key, value)
VALUES ('risk_max_open_positions_per_strategy', '80'::jsonb)
ON CONFLICT (key) DO NOTHING;
