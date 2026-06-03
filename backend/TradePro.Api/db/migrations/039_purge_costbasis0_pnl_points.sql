-- 039_purge_costbasis0_pnl_points.sql
--
-- One-time cleanup of cost-basis-0 artifact rows in paper_pnl_points.
--
-- A position whose avg_entry_price was 0 had its unrealised computed as
-- (mark - 0) * qty = the position NOTIONAL, not P&L. Summed into the
-- strategy's unrealised_pnl, this wrote points like +27,186 for a ~$50k
-- desk (the "P&L graph reads -$26k" bug). The ingest path now drops
-- cost-basis-0 positions (PostgresPaperSnapshotStore.CleanUnrealised), and
-- the frontend already filters |total| >= 15k — this purges the historical
-- rows so the raw series is clean too.
--
-- These demo books run ~$50k of capital; no legitimate single-day OPEN
-- mark-to-market reaches $15k, so the magnitude filter only ever removes
-- the notional artifact (matches the validated frontend threshold).

DELETE FROM paper_pnl_points
WHERE ABS(unrealised_pnl) >= 15000;
