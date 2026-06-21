using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/options/* — the Options Desk (wheel) surfaces. v1: the candidate
/// SCREEN snapshot. The Mac-side `tradepro-options-screen` job computes IV-Rank
/// (IBKR) + regime (bar cache) + runs the risk engine, then POSTs the whole
/// screen here; the Options tab GETs it. Mirrors the bar-cache-health feed
/// (Mac computes → API stores → frontend reads). Empty default until the first
/// screen runs — never a 404 the UI has to special-case.
/// </summary>
public static class OptionsEndpoints
{
    private const string EmptyScreen =
        "{\"generated_at_utc\":null,\"market_open\":false,\"candidates\":[]}";

    public static IEndpointRouteBuilder MapOptionsEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/options").WithTags("Options");

        // Latest wheel candidate screen (empty default if none pushed yet).
        g.MapGet("/candidates", async (NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var json = await conn.ExecuteScalarAsync<string?>(
                "SELECT payload::text FROM options_candidate_screen WHERE id = 1");
            return Results.Content(
                string.IsNullOrWhiteSpace(json) ? EmptyScreen : json!, "application/json");
        });

        // Mac screen job pushes the latest screen (whole payload, verbatim JSONB).
        g.MapPost("/candidates", async (HttpContext ctx, NpgsqlDataSource db) =>
        {
            using var reader = new StreamReader(ctx.Request.Body);
            var body = await reader.ReadToEndAsync();
            if (string.IsNullOrWhiteSpace(body))
                return Results.BadRequest(new { error = "empty body" });
            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO options_candidate_screen (id, payload, updated_at_utc)
                VALUES (1, @p::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    payload = EXCLUDED.payload, updated_at_utc = NOW();",
                new { p = body });
            return Results.Ok(new { ok = true });
        });

        return app;
    }
}
