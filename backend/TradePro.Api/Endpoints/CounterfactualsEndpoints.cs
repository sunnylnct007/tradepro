using System.Text.Json;
using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/counterfactuals — the Evidence Store's scored-replay table (spec §7.0:
/// "store this valuable run info and collect as much as we go to make our
/// system mature"). A counterfactual run answers "what would have happened":
/// a skipped-signal cohort replayed against real prices, or an event study
/// (e.g. "what happens to META after an 8% single-day drop, historically").
/// Same shape as VerdictsEndpoints — raw Dapper over NpgsqlDataSource, no
/// dedicated service layer needed for a surface this small.
/// </summary>
public static class CounterfactualsEndpoints
{
    public static IEndpointRouteBuilder MapCounterfactualsEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/counterfactuals").WithTags("Counterfactuals");

        group.MapPost("/", async (CounterfactualPostBody body, NpgsqlDataSource db, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(body.Kind))
                return Results.BadRequest(new { error = "kind is required" });
            if (body.Results is null)
                return Results.BadRequest(new { error = "results is required" });

            await using var conn = await db.OpenConnectionAsync(ct);
            var id = await conn.ExecuteScalarAsync<long>(new CommandDefinition(@"
                INSERT INTO counterfactual_runs
                    (user_id, kind, symbol, generated_by, cohort, method, results, findings, caveats, spec_version)
                VALUES
                    (@user_id, @kind, @symbol, @generated_by, @cohort::jsonb, @method::jsonb,
                     @results::jsonb, @findings::jsonb, @caveats::jsonb, @spec_version)
                RETURNING id;",
                new
                {
                    user_id = body.UserId ?? 1,
                    kind = body.Kind,
                    symbol = string.IsNullOrWhiteSpace(body.Symbol) ? null : body.Symbol.Trim().ToUpperInvariant(),
                    generated_by = string.IsNullOrWhiteSpace(body.GeneratedBy) ? "manual" : body.GeneratedBy,
                    cohort = body.Cohort.HasValue ? body.Cohort.Value.GetRawText() : null,
                    method = body.Method.HasValue ? body.Method.Value.GetRawText() : null,
                    results = body.Results.Value.GetRawText(),
                    findings = body.Findings.HasValue ? body.Findings.Value.GetRawText() : null,
                    caveats = body.Caveats.HasValue ? body.Caveats.Value.GetRawText() : null,
                    spec_version = body.SpecVersion,
                },
                cancellationToken: ct));

            return Results.Created($"/api/counterfactuals/{id}", new { id });
        });

        // GET /api/counterfactuals?symbol=&kind= — list, newest first.
        group.MapGet("/", async (
            string? symbol, string? kind, int? limit,
            NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(new CommandDefinition(@"
                SELECT id, user_id, run_ts, kind, symbol, generated_by,
                       cohort::text AS cohort, method::text AS method,
                       results::text AS results, findings::text AS findings,
                       caveats::text AS caveats, spec_version
                FROM counterfactual_runs
                WHERE (@symbol IS NULL OR symbol = @symbol)
                  AND (@kind IS NULL OR kind = @kind)
                ORDER BY run_ts DESC
                LIMIT @limit;",
                new
                {
                    symbol = string.IsNullOrWhiteSpace(symbol) ? null : symbol.Trim().ToUpperInvariant(),
                    kind = string.IsNullOrWhiteSpace(kind) ? null : kind,
                    limit = Math.Clamp(limit ?? 100, 1, 1000),
                },
                cancellationToken: ct));
            return Results.Ok(new { runs = rows });
        });

        group.MapGet("/{id:long}", async (long id, NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var row = await conn.QuerySingleOrDefaultAsync(new CommandDefinition(@"
                SELECT id, user_id, run_ts, kind, symbol, generated_by,
                       cohort::text AS cohort, method::text AS method,
                       results::text AS results, findings::text AS findings,
                       caveats::text AS caveats, spec_version
                FROM counterfactual_runs WHERE id = @id;",
                new { id }, cancellationToken: ct));
            return row is null
                ? Results.NotFound(new { error = $"no counterfactual run {id}" })
                : Results.Ok(new { run = row });
        });

        return app;
    }

    public sealed record CounterfactualPostBody(
        int? UserId,
        string Kind,
        string? Symbol,
        string? GeneratedBy,
        JsonElement? Cohort,
        JsonElement? Method,
        JsonElement? Results,
        JsonElement? Findings,
        JsonElement? Caveats,
        string? SpecVersion
    );
}
