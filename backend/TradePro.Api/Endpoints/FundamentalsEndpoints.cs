using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/fundamentals — P/E, ROE, margins and the reported EPS trend (migration 067).
///
/// Owner, 29 Aug 2026: "if i have to decide for trades for next week I will look
/// at the fundamentals of that company" and "this figure shd be visible on our
/// data harvesting screen as well".
///
/// A CURRENT snapshot. Right for a human deciding today; WRONG for a backtest,
/// because today's P/E on a past date is look-ahead. Every response says so.
/// </summary>
public static class FundamentalsEndpoints
{
    public static IEndpointRouteBuilder MapFundamentalsEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/fundamentals").WithTags("Fundamentals");

        // Whole store, for the harvest screen. Small (one row per symbol), so it
        // is served in one call rather than N per-symbol lookups behind a table.
        g.MapGet("/", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync(@"
                SELECT symbol, as_of, source,
                       trailing_pe AS trailingPe, forward_pe AS forwardPe,
                       price_to_book AS priceToBook, return_on_equity AS returnOnEquity,
                       profit_margin AS profitMargin, debt_to_equity AS debtToEquity,
                       trailing_eps AS trailingEps, forward_eps AS forwardEps,
                       market_cap AS marketCap, annual_eps::text AS annualEps
                FROM fundamentals ORDER BY symbol;")).AsList();
            return Results.Ok(new
            {
                count = rows.Count,
                // Coverage, so an empty table reads as "nothing harvested yet"
                // rather than "these companies have no fundamentals".
                lastHarvestUtc = rows.Count == 0 ? null
                    : rows.Max(r => (DateTime?)((IDictionary<string, object>)r)["as_of"]),
                note = "CURRENT snapshot — decision support, never a backtest input.",
                fundamentals = rows,
            });
        })
        .WithName("GetFundamentals");

        // Bulk upsert from the harvester. One row per symbol; re-running replaces.
        g.MapPost("/", async (List<FundamentalRow> rows, NpgsqlDataSource db) =>
        {
            if (rows is null || rows.Count == 0)
                return Results.BadRequest(new { error = "no rows" });
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(@"
                INSERT INTO fundamentals
                  (symbol, as_of, source, trailing_pe, forward_pe, price_to_book,
                   return_on_equity, profit_margin, debt_to_equity,
                   trailing_eps, forward_eps, market_cap, annual_eps)
                VALUES
                  (@Symbol, now(), @Source, @TrailingPe, @ForwardPe, @PriceToBook,
                   @ReturnOnEquity, @ProfitMargin, @DebtToEquity,
                   @TrailingEps, @ForwardEps, @MarketCap, CAST(@AnnualEps AS jsonb))
                ON CONFLICT (symbol) DO UPDATE SET
                   as_of = now(), source = EXCLUDED.source,
                   trailing_pe = EXCLUDED.trailing_pe,
                   forward_pe = EXCLUDED.forward_pe,
                   price_to_book = EXCLUDED.price_to_book,
                   return_on_equity = EXCLUDED.return_on_equity,
                   profit_margin = EXCLUDED.profit_margin,
                   debt_to_equity = EXCLUDED.debt_to_equity,
                   trailing_eps = EXCLUDED.trailing_eps,
                   forward_eps = EXCLUDED.forward_eps,
                   market_cap = EXCLUDED.market_cap,
                   annual_eps = EXCLUDED.annual_eps;", rows);
            return Results.Ok(new { upserted = n });
        })
        .WithName("UpsertFundamentals");

        return app;
    }
}

/// <summary>One symbol's current fundamentals, as posted by the harvester.
/// Every metric is nullable: an ETF has no P/E by construction, and a null must
/// stay distinguishable from a zero.</summary>
public sealed record FundamentalRow(
    string Symbol,
    string? Source,
    decimal? TrailingPe,
    decimal? ForwardPe,
    decimal? PriceToBook,
    decimal? ReturnOnEquity,
    decimal? ProfitMargin,
    decimal? DebtToEquity,
    decimal? TrailingEps,
    decimal? ForwardEps,
    decimal? MarketCap,
    string? AnnualEps
);
