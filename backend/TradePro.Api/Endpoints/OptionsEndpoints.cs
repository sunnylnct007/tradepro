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

        // ── Paper wheel positions (BRD §11 ledger) ──────────────────
        // Record a paper CSP entry + its risk-engine verdict; list/track them.
        g.MapGet("/positions", async (NpgsqlDataSource db, string? state) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync(@"
                SELECT id, symbol, structure, state, strike::float8, expiry, dte,
                       delta::float8, iv_rank::float8, premium::float8, contracts,
                       cash_secured_gbp::float8, regime, opened_at_utc, closed_at_utc,
                       realised_pnl_gbp::float8, notes, risk_decision::text AS risk_decision_json,
                       updated_at_utc
                FROM options_paper_position
                WHERE (@state IS NULL OR state = @state)
                ORDER BY opened_at_utc DESC;",
                new { state });
            return Results.Ok(new { positions = rows.AsList() });
        });

        g.MapPost("/positions", async (PaperPositionBody body, NpgsqlDataSource db) =>
        {
            if (string.IsNullOrWhiteSpace(body.Symbol))
                return Results.BadRequest(new { error = "symbol required" });
            await using var conn = await db.OpenConnectionAsync();
            var id = await conn.ExecuteScalarAsync<long>(@"
                INSERT INTO options_paper_position
                    (symbol, structure, state, strike, expiry, dte, delta, iv_rank,
                     premium, contracts, cash_secured_gbp, regime, notes, risk_decision)
                VALUES
                    (@Symbol, COALESCE(@Structure,'CASH_SECURED_PUT'), COALESCE(@State,'SHORT_PUT_OPEN'),
                     @Strike, @Expiry::date, @Dte, @Delta, @IvRank,
                     @Premium, COALESCE(@Contracts,1), @CashSecuredGbp, @Regime, @Notes,
                     @RiskDecision::jsonb)
                RETURNING id;",
                new
                {
                    body.Symbol, body.Structure, body.State, body.Strike, body.Expiry,
                    body.Dte, body.Delta, body.IvRank, body.Premium, body.Contracts,
                    body.CashSecuredGbp, body.Regime, body.Notes,
                    RiskDecision = string.IsNullOrWhiteSpace(body.RiskDecisionJson) ? null : body.RiskDecisionJson,
                });
            return Results.Ok(new { ok = true, id });
        });

        // State transition / close (e.g. assigned, rolled, closed with P&L).
        g.MapPost("/positions/{id:long}/event", async (long id, PaperPositionEventBody body, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(@"
                UPDATE options_paper_position SET
                    state            = COALESCE(@State, state),
                    realised_pnl_gbp = COALESCE(@RealisedPnlGbp, realised_pnl_gbp),
                    closed_at_utc    = CASE WHEN @State = 'CLOSED' THEN NOW() ELSE closed_at_utc END,
                    notes            = COALESCE(@Notes, notes),
                    updated_at_utc   = NOW()
                WHERE id = @id;",
                new { id, body.State, body.RealisedPnlGbp, body.Notes });
            return n == 0 ? Results.NotFound(new { error = "no such position" }) : Results.Ok(new { ok = true });
        });

        // Remove a paper position (mis-entry / cleanup). Paper ledger only.
        g.MapDelete("/positions/{id:long}", async (long id, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var n = await conn.ExecuteAsync(
                "DELETE FROM options_paper_position WHERE id = @id;", new { id });
            return n == 0 ? Results.NotFound(new { error = "no such position" }) : Results.Ok(new { ok = true });
        });

        return app;
    }

    public sealed record PaperPositionBody(
        string Symbol, string? Structure, string? State, decimal? Strike, string? Expiry,
        int? Dte, decimal? Delta, decimal? IvRank, decimal? Premium, int? Contracts,
        decimal? CashSecuredGbp, string? Regime, string? Notes, string? RiskDecisionJson);

    public sealed record PaperPositionEventBody(string? State, decimal? RealisedPnlGbp, string? Notes);
}
