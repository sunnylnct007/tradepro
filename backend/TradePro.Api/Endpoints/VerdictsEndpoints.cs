using System.Text.Json;
using Dapper;
using Npgsql;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/verdicts — the verdict store (TRADEPRO_REDESIGN_SPEC.md §5). System of
/// record for every swing/wheel/invest call Symbol Lab (or any future caller)
/// makes, logged with its counter-case up front and scored against what
/// actually happened once its horizon passes. Raw Dapper over NpgsqlDataSource,
/// same shape as AdminEndpoints' strategy-broker-map — no dedicated service
/// layer needed yet for a straight CRUD + aggregate surface this small.
/// </summary>
public static class VerdictsEndpoints
{
    public static IEndpointRouteBuilder MapVerdictsEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/verdicts").WithTags("Verdicts");

        // POST /api/verdicts — body = row minus id/ts. counter_case is
        // mandatory (spec: "no verdict without the argument against it").
        group.MapPost("/", async (VerdictPostBody body, NpgsqlDataSource db, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(body.Symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            if (string.IsNullOrWhiteSpace(body.Horizon)
                || !new[] { "swing", "wheel", "invest" }.Contains(body.Horizon))
                return Results.BadRequest(new { error = "horizon must be one of: swing, wheel, invest" });
            if (string.IsNullOrWhiteSpace(body.Verdict)
                || !new[] { "YES", "NO", "NOT_YET", "WAIT" }.Contains(body.Verdict))
                return Results.BadRequest(new { error = "verdict must be one of: YES, NO, NOT_YET, WAIT" });
            if (string.IsNullOrWhiteSpace(body.Why))
                return Results.BadRequest(new { error = "why is required" });
            if (string.IsNullOrWhiteSpace(body.CounterCase))
                return Results.BadRequest(new { error = "counter_case is required — no verdict without the argument against it" });
            if (body.Evidence is null)
                return Results.BadRequest(new { error = "evidence is required" });

            await using var conn = await db.OpenConnectionAsync(ct);
            var id = await conn.ExecuteScalarAsync<long>(new CommandDefinition(@"
                INSERT INTO verdicts
                    (user_id, symbol, spot, horizon, verdict, spec, why, counter_case, evidence, source)
                VALUES
                    (@user_id, @symbol, @spot, @horizon, @verdict, @spec::jsonb, @why, @counter_case, @evidence::jsonb, @source)
                RETURNING id;",
                new
                {
                    user_id = body.UserId ?? 1,
                    symbol = body.Symbol.Trim().ToUpperInvariant(),
                    spot = body.Spot,
                    horizon = body.Horizon,
                    verdict = body.Verdict,
                    spec = body.Spec.HasValue ? body.Spec.Value.GetRawText() : null,
                    why = body.Why,
                    counter_case = body.CounterCase,
                    evidence = body.Evidence.Value.GetRawText(),
                    source = string.IsNullOrWhiteSpace(body.Source) ? "symbol_lab" : body.Source,
                },
                cancellationToken: ct));

            return Results.Created($"/api/verdicts/{id}", new { id });
        });

        // GET /api/verdicts?symbol=&horizon=&from= — list, newest first.
        group.MapGet("/", async (
            string? symbol, string? horizon, DateTime? from, int? limit,
            NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(new CommandDefinition(@"
                SELECT id, user_id, ts, symbol, spot, horizon, verdict,
                       spec::text AS spec, why, counter_case,
                       evidence::text AS evidence, source
                FROM verdicts
                WHERE (@symbol IS NULL OR symbol = @symbol)
                  AND (@horizon IS NULL OR horizon = @horizon)
                  AND (@from IS NULL OR ts >= @from)
                ORDER BY ts DESC
                LIMIT @limit;",
                new
                {
                    symbol = string.IsNullOrWhiteSpace(symbol) ? null : symbol.Trim().ToUpperInvariant(),
                    horizon = string.IsNullOrWhiteSpace(horizon) ? null : horizon,
                    from,
                    limit = Math.Clamp(limit ?? 100, 1, 1000),
                },
                cancellationToken: ct));
            return Results.Ok(new { verdicts = rows });
        });

        group.MapGet("/{id:long}", async (long id, NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var row = await conn.QuerySingleOrDefaultAsync(new CommandDefinition(@"
                SELECT id, user_id, ts, symbol, spot, horizon, verdict,
                       spec::text AS spec, why, counter_case,
                       evidence::text AS evidence, source
                FROM verdicts WHERE id = @id;",
                new { id }, cancellationToken: ct));
            if (row is null)
                return Results.NotFound(new { error = $"no verdict {id}" });
            var outcomes = await conn.QueryAsync(new CommandDefinition(@"
                SELECT horizon_days, scored_at, ref_price, return_pct, outcome
                FROM verdict_outcomes WHERE verdict_id = @id ORDER BY horizon_days NULLS FIRST;",
                new { id }, cancellationToken: ct));
            return Results.Ok(new { verdict = row, outcomes });
        });

        // GET /api/verdicts/scorecard — per-horizon hit-rate/avg-return, only
        // over SCORED outcomes (outcome NOT IN ('PENDING', NULL) — an unscored
        // verdict must never silently count as a loss or get excluded in a way
        // that inflates the rate; it just doesn't contribute yet).
        group.MapGet("/scorecard", async (NpgsqlDataSource db, CancellationToken ct) =>
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var rows = await conn.QueryAsync(new CommandDefinition(@"
                SELECT
                    v.horizon,
                    count(*) FILTER (WHERE o.outcome IS NOT NULL AND o.outcome <> 'PENDING') AS scored_count,
                    count(*) FILTER (WHERE o.outcome IN ('WIN', 'ASSIGNED')) AS win_count,
                    count(*) FILTER (WHERE o.outcome IN ('LOSS')) AS loss_count,
                    avg(o.return_pct) FILTER (WHERE o.outcome IS NOT NULL AND o.outcome <> 'PENDING') AS avg_return_pct
                FROM verdicts v
                LEFT JOIN verdict_outcomes o ON o.verdict_id = v.id
                GROUP BY v.horizon
                ORDER BY v.horizon;",
                cancellationToken: ct));
            return Results.Ok(new { scorecard = rows });
        });

        return app;
    }

    public sealed record VerdictPostBody(
        int? UserId,
        string Symbol,
        decimal Spot,
        string Horizon,
        string Verdict,
        JsonElement? Spec,
        string Why,
        string CounterCase,
        JsonElement? Evidence,
        string? Source
    );
}
