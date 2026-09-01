using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/strangle-decisions — the durable record of every strangle evaluation.
///
/// Owner, 31 Aug 2026: "i need the stuff to be logged for analysis later on so
/// we might need a history table to store these evaluations and decisions ...
/// so we can evaluate what we did and why we did it and check if it was right
/// or not".
///
/// WHY THIS EXISTS AT ALL. The Lambda writes its ledger to /tmp, which is wiped
/// between invocations — so every scheduled decision since the move to Lambda
/// has been lost. The forward test has been recording nothing, which is the one
/// thing it was created to do.
///
/// STAND-ASIDES ARE STORED, and they are the valuable rows. This strategy's
/// edge is what it REFUSES to trade; a log of only the trades cannot tell you
/// whether the gate is set correctly. The same argument is already made in the
/// paper-record file's shadow-recording note.
/// </summary>
public static class StrangleDecisionLogEndpoints
{
    public sealed record DecisionRow(
        string Market, DateTime AsOf, string Decision, string Reason,
        DateTime? ExchangeDate = null, string? VolSymbol = null,
        decimal? VolIndex = null, decimal? VolThreshold = null,
        decimal? IvUsedPct = null, decimal? Spot = null, string? SpotBasis = null,
        bool Provisional = false, string? SessionState = null,
        string? ExpiryKind = null, int? Dte = null,
        decimal? PutStrike = null, decimal? CallStrike = null,
        decimal? Forward = null, int? Lot = null,
        decimal? Collateral = null, decimal? MarginEstimate = null,
        decimal? CreditModelled = null, string? JobsCommit = null,
        string? Detail = null, decimal? VolAtDecision = null,
        string? DataSource = null);

    /// <summary>What actually happened to a decision — placement and exit.
    /// Every field nullable: placement follows the decision, and the exit
    /// follows that by hours. "Not yet known" must be representable, because
    /// grading a row before its session closes is lookahead.</summary>
    public sealed record ExecutionRow(
        string Market, DateTime AsOf, string? ExpiryKind = null,
        bool? Placed = null, bool? Partial = null, bool? Shadow = null,
        string? BrokerOrderIds = null, decimal? CreditActual = null,
        DateTime? PlacedAtUtc = null, decimal? ExitCostActual = null,
        string? CloseTrigger = null, DateTime? ClosedAtUtc = null,
        decimal? RealisedPnl = null);

    public static IEndpointRouteBuilder MapStrangleDecisionLogEndpoints(
        this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/strangle-decisions").WithTags("StrangleDecisions");

        // POST — upsert one or many. UPSERT, not insert: a re-run (a UI
        // trigger, a retry after a failure) must not double-count, or every
        // summary computed over this table is silently wrong.
        g.MapPost("/", async (DecisionRow[] rows, NpgsqlDataSource db, CancellationToken ct) =>
        {
            if (rows is null || rows.Length == 0)
                return Results.BadRequest(new { error = "no rows" });
            await using var conn = await db.OpenConnectionAsync(ct);
            var n = await conn.ExecuteAsync(@"
                INSERT INTO strangle_decision_log
                    (market, as_of, exchange_date, decision, reason, vol_symbol,
                     vol_index, vol_threshold, iv_used_pct, spot, spot_basis,
                     provisional, session_state, expiry_kind, dte, put_strike,
                     call_strike, forward, lot, collateral, margin_estimate,
                     credit_modelled, jobs_commit, detail, vol_at_decision, data_source)
                VALUES
                    (@Market, @AsOf, @ExchangeDate, @Decision, @Reason, @VolSymbol,
                     @VolIndex, @VolThreshold, @IvUsedPct, @Spot, @SpotBasis,
                     @Provisional, @SessionState, @ExpiryKind, @Dte, @PutStrike,
                     @CallStrike, @Forward, @Lot, @Collateral, @MarginEstimate,
                     @CreditModelled, @JobsCommit, @Detail::jsonb, @VolAtDecision, @DataSource)
                -- Keyed on the session being TRADED, not the settled session the gate
                -- read. Those diverge, and keying on as_of silently destroyed a
                -- day of Indian decisions on 1 Sep 2026 (migration 073).
                ON CONFLICT (market, COALESCE(exchange_date, as_of), COALESCE(expiry_kind, '')) DO UPDATE SET
                     as_of         = EXCLUDED.as_of,
                     decision      = EXCLUDED.decision,
                     reason        = EXCLUDED.reason,
                     vol_index     = EXCLUDED.vol_index,
                     vol_threshold = EXCLUDED.vol_threshold,
                     iv_used_pct   = EXCLUDED.iv_used_pct,
                     spot          = EXCLUDED.spot,
                     spot_basis    = EXCLUDED.spot_basis,
                     provisional   = EXCLUDED.provisional,
                     session_state = EXCLUDED.session_state,
                     put_strike    = EXCLUDED.put_strike,
                     call_strike   = EXCLUDED.call_strike,
                     forward       = EXCLUDED.forward,
                     collateral    = EXCLUDED.collateral,
                     margin_estimate = EXCLUDED.margin_estimate,
                     credit_modelled = EXCLUDED.credit_modelled,
                     vol_at_decision = EXCLUDED.vol_at_decision,
                     data_source     = EXCLUDED.data_source,
                     jobs_commit   = EXCLUDED.jobs_commit,
                     detail        = EXCLUDED.detail,
                     decided_at_utc = now();", rows);
            return Results.Ok(new { ok = true, rows = n });
        });

        // POST /execution — record what ACTUALLY happened to a decision.
        //
        // Owner, 31 Aug 2026: "f the strangell worked or not". The platform
        // could not answer that from its own records — the decision log stops
        // at the decision. Nothing said whether the order was placed, what we
        // were FILLED at, or what it cost to close, so both figures had to be
        // reconstructed from the broker by hand.
        //
        // Keyed on the SAME (market, as_of, expiry_kind) the decision upsert
        // uses, so an execution can only ever attach to a decision that was
        // genuinely recorded first. An execution with no decision is REFUSED
        // rather than inserted: a fill with no recorded reasoning is exactly
        // the row that makes a forward test unauditable.
        g.MapPost("/execution", async (ExecutionRow row, NpgsqlDataSource db,
                                       CancellationToken ct) =>
        {
            if (row is null || string.IsNullOrWhiteSpace(row.Market))
                return Results.BadRequest(new { error = "market required" });

            await using var conn = await db.OpenConnectionAsync(ct);
            var n = await conn.ExecuteAsync(@"
                UPDATE strangle_decision_log SET
                    placed           = COALESCE(@Placed, placed),
                    partial          = COALESCE(@Partial, partial),
                    shadow           = COALESCE(@Shadow, shadow),
                    broker_order_ids = COALESCE(@BrokerOrderIds, broker_order_ids),
                    credit_actual    = COALESCE(@CreditActual, credit_actual),
                    placed_at_utc    = COALESCE(@PlacedAtUtc, placed_at_utc),
                    exit_cost_actual = COALESCE(@ExitCostActual, exit_cost_actual),
                    close_trigger    = COALESCE(@CloseTrigger, close_trigger),
                    closed_at_utc    = COALESCE(@ClosedAtUtc, closed_at_utc),
                    realised_pnl     = COALESCE(@RealisedPnl, realised_pnl)
                WHERE market = @Market
                  AND as_of  = @AsOf
                  AND COALESCE(expiry_kind, '') = COALESCE(@ExpiryKind, '');", row);

            if (n == 0)
                // FAIL LOUD. Silently inserting would create a fill with no
                // recorded reasoning — unauditable, and worse than no row.
                return Results.Json(new
                {
                    ok = false,
                    error = $"no decision recorded for {row.Market} {row.AsOf:yyyy-MM-dd} "
                          + $"[{row.ExpiryKind}] — an execution cannot be attached to a "
                          + "decision that was never logged",
                }, statusCode: 404);

            return Results.Ok(new { ok = true, updated = n });
        });

        // GET — the history, newest first. `market` and `decision` narrow it;
        // `days` bounds it. Stand-asides are INCLUDED by default, deliberately.
        g.MapGet("/", async (NpgsqlDataSource db, string? market, string? decision,
                             int days, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(@"
                SELECT market, as_of, exchange_date, decided_at_utc, decision, reason,
                       vol_symbol, vol_index::float8 AS vol_index,
                       vol_threshold::float8 AS vol_threshold,
                       spot::float8 AS spot, spot_basis, provisional, session_state,
                       expiry_kind, dte, put_strike::float8 AS put_strike,
                       call_strike::float8 AS call_strike,
                       forward::float8 AS forward,
                       vol_at_decision::float8 AS vol_at_decision, data_source,
                       collateral::float8 AS collateral,
                       -- The MONEY columns were written but never selected, so
                       -- credit_modelled and lot read as NULL through the API
                       -- while sitting populated in the table. Asked how much
                       -- the system would have made, the log could not answer
                       -- from data it already held. (No double quotes in here:
                       -- this is a C# verbatim string and a bare quote ends it.)
                       margin_estimate::float8 AS margin_estimate,
                       credit_modelled::float8 AS credit_modelled, lot,
                       -- EXECUTION — added in 072 and likewise never selected,
                       -- which made the whole decision->execution link
                       -- invisible to every reader of this endpoint.
                       placed, partial, shadow, broker_order_ids,
                       credit_actual::float8 AS credit_actual, placed_at_utc,
                       exit_cost_actual::float8 AS exit_cost_actual,
                       close_trigger, closed_at_utc,
                       realised_pnl::float8 AS realised_pnl,
                       index_close::float8 AS index_close,
                       outcome_pct::float8 AS outcome_pct, outcome_note, graded_at_utc,
                       jobs_commit
                  FROM strangle_decision_log
                 WHERE (@market IS NULL OR market = @market)
                   AND (@decision IS NULL OR decision = @decision)
                   AND as_of >= (CURRENT_DATE - (@days || ' days')::interval)
                 ORDER BY as_of DESC, market
                 LIMIT 2000;",
                new { market, decision, days = days <= 0 ? 90 : days });
            return Results.Ok(new { rows = rows.AsList() });
        });

        // GET /summary — "was it right?", per market.
        //
        // Reports traded AND declined counts side by side. A summary that shows
        // only what was traded cannot answer whether the gate was set correctly,
        // which is the actual question being asked of this table.
        g.MapGet("/summary", async (NpgsqlDataSource db, int days, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(@"
                SELECT market,
                       COUNT(*)                                        AS evaluated,
                       COUNT(*) FILTER (WHERE decision = 'CANDIDATE')  AS traded,
                       COUNT(*) FILTER (WHERE decision = 'STAND_ASIDE') AS declined,
                       COUNT(*) FILTER (WHERE provisional)             AS provisional,
                       COUNT(*) FILTER (WHERE graded_at_utc IS NOT NULL) AS graded,
                       AVG(outcome_pct)::float8                        AS mean_outcome_pct,
                       MIN(outcome_pct)::float8                        AS worst_outcome_pct
                  FROM strangle_decision_log
                 WHERE as_of >= (CURRENT_DATE - (@days || ' days')::interval)
                 GROUP BY market
                 ORDER BY market;",
                new { days = days <= 0 ? 90 : days });
            return Results.Ok(new
            {
                rows = rows.AsList(),
                note = "declined rows are included on purpose — the gate is the "
                     + "strategy, and only the refusals show whether it is set right",
            });
        });

        return app;
    }
}
