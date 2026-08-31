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
        string? Detail = null);

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
                     credit_modelled, jobs_commit, detail)
                VALUES
                    (@Market, @AsOf, @ExchangeDate, @Decision, @Reason, @VolSymbol,
                     @VolIndex, @VolThreshold, @IvUsedPct, @Spot, @SpotBasis,
                     @Provisional, @SessionState, @ExpiryKind, @Dte, @PutStrike,
                     @CallStrike, @Forward, @Lot, @Collateral, @MarginEstimate,
                     @CreditModelled, @JobsCommit, @Detail::jsonb)
                ON CONFLICT (market, as_of, COALESCE(expiry_kind, '')) DO UPDATE SET
                     exchange_date = EXCLUDED.exchange_date,
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
                     jobs_commit   = EXCLUDED.jobs_commit,
                     detail        = EXCLUDED.detail,
                     decided_at_utc = now();", rows);
            return Results.Ok(new { ok = true, rows = n });
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
                       collateral::float8 AS collateral,
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
