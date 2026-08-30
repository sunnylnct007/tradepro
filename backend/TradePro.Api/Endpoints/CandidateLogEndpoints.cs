using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/candidate-log — what each screen PUBLISHED, per day (migration 068).
///
/// The forward-test record. today_setups_results holds only 'latest' and is
/// overwritten every run, so nothing accumulated; this is the append-only half.
/// It records candidates, NOT orders, so the two can be compared later: did the
/// fill land where the screen said it would.
/// </summary>
public static class CandidateLogEndpoints
{
    public static IEndpointRouteBuilder MapCandidateLogEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/candidate-log").WithTags("CandidateLog");

        // History for one strategy. `days` bounds it; coverage is returned
        // alongside so an empty result reads as "nothing published yet"
        // rather than "this strategy finds nothing".
        g.MapGet("/{strategy}", async (string strategy, int? days, NpgsqlDataSource db) =>
        {
            var d = days is > 0 and <= 3650 ? days.Value : 90;
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync(@"
                SELECT strategy, symbol, signal_date, published_at, spot, strike,
                       target_price, stop_price, dte, annual_vol_pct, size_factor,
                       collateral, detail::text AS detail
                FROM strategy_candidate_log
                WHERE strategy = @strategy
                  AND signal_date >= CURRENT_DATE - @d
                ORDER BY signal_date DESC, symbol;",
                new { strategy, d })).AsList();
            var cov = await conn.QuerySingleAsync(@"
                SELECT COUNT(*)::int AS rows, COUNT(DISTINCT signal_date)::int AS days,
                       MIN(signal_date) AS first_date, MAX(signal_date) AS last_date
                FROM strategy_candidate_log WHERE strategy = @strategy;",
                new { strategy });
            return Results.Ok(new
            {
                strategy,
                windowDays = d,
                count = rows.Count,
                coverage = cov,
                note = "What the screen PUBLISHED, not what was traded.",
                candidates = rows,
            });
        })
        .WithName("GetCandidateLog");

        // Upsert on (strategy, symbol, signal_date). The screen runs several
        // times a session and must not multiply rows; a LATER run of the same
        // day WINS, because the last run before the close is the most accurate
        // view of that session.
        g.MapPost("/", async (List<CandidateLogRow> rows, NpgsqlDataSource db) =>
        {
            if (rows is null || rows.Count == 0)
                return Results.BadRequest(new { error = "no rows" });
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(@"
                INSERT INTO strategy_candidate_log
                  (strategy, symbol, signal_date, published_at, spot, strike,
                   target_price, stop_price, dte, annual_vol_pct, size_factor,
                   collateral, detail)
                VALUES
                  (@Strategy, @Symbol, @SignalDate, now(), @Spot, @Strike,
                   @TargetPrice, @StopPrice, @Dte, @AnnualVolPct, @SizeFactor,
                   @Collateral, CAST(@Detail AS jsonb))
                ON CONFLICT (strategy, symbol, signal_date) DO UPDATE SET
                   published_at = now(), spot = EXCLUDED.spot, strike = EXCLUDED.strike,
                   target_price = EXCLUDED.target_price, stop_price = EXCLUDED.stop_price,
                   dte = EXCLUDED.dte, annual_vol_pct = EXCLUDED.annual_vol_pct,
                   size_factor = EXCLUDED.size_factor, collateral = EXCLUDED.collateral,
                   detail = EXCLUDED.detail;", rows);
            return Results.Ok(new { upserted = n });
        })
        .WithName("UpsertCandidateLog");

        return app;
    }
}

/// <summary>One published candidate. Every metric nullable — a screen may not
/// produce all of them, and NULL must stay distinguishable from zero.</summary>
public sealed record CandidateLogRow(
    string Strategy,
    string Symbol,
    DateTime SignalDate,
    decimal? Spot,
    decimal? Strike,
    decimal? TargetPrice,
    decimal? StopPrice,
    int? Dte,
    decimal? AnnualVolPct,
    decimal? SizeFactor,
    decimal? Collateral,
    string? Detail
);
