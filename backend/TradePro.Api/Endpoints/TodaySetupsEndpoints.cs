using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/today-setups/* — read API for the "Today's Setups" scanner artifact
/// produced by `tradepro-today-setups` on the Mac (screens a universe →
/// per-symbol Ichimoku signal + range/risk → ranked by ENTRY QUALITY:
/// ⭐ consider / ⚠ extended / excluded). Powers the dashboard scanner card —
/// the curated, risk-aware replacement for the original flat BUY-light board.
///
/// Mac worker pushes the artifact via /api/ingest/today-setups (IngestToken).
/// Keyed by (universe, label) — same store pattern as equity-pipeline / fill-replay.
/// </summary>
public static class TodaySetupsEndpoints
{
    public static IEndpointRouteBuilder MapTodaySetupsUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/today-setups").WithTags("TodaySetups");

        // GET /api/today-setups/{universe}/latest
        group.MapGet("/{universe}/latest", async (
            string universe, string? label, NpgsqlDataSource db) =>
        {
            var l = string.IsNullOrWhiteSpace(label) ? "latest" : label;
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<SetupsRow>(@"
                SELECT artifact::text AS artifact_text,
                       as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM today_setups_results
                WHERE universe = @universe AND label = @label
                LIMIT 1;",
                new { universe, label = l });
            if (row is null)
            {
                return Results.NotFound(new
                {
                    error = $"no today-setups artifact for {universe} (label={l})",
                    hint = "run `tradepro-today-setups --push` on the worker host",
                });
            }
            return Results.Ok(new
            {
                universe,
                label = l,
                asOfUtc = row.as_of_utc,
                uploadedAtUtc = row.uploaded_at_utc,
                uploadedBy = row.uploaded_by,
                note = row.note,
                artifact = JsonbHelpers.FromJsonb(row.artifact_text),
            });
        });

        return app;
    }

    public static IEndpointRouteBuilder MapTodaySetupsIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("TodaySetups/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/today-setups
        // Body: { "universe": "large_50", "label": "latest", "uploaded_by": "...",
        //         "note": "...", "artifact": { ...CLI emit... } }
        group.MapPost("/today-setups", async (
            System.Text.Json.JsonElement payload, NpgsqlDataSource db) =>
        {
            if (payload.ValueKind != System.Text.Json.JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            var universe = JsonbHelpers.ReadString(payload, "universe");
            if (string.IsNullOrWhiteSpace(universe))
                return Results.BadRequest(new { error = "universe is required" });
            var label = JsonbHelpers.ReadString(payload, "label") ?? "latest";
            var uploadedBy = JsonbHelpers.ReadString(payload, "uploaded_by");
            var note = JsonbHelpers.ReadString(payload, "note");

            if (!payload.TryGetProperty("artifact", out var artifact)
                || artifact.ValueKind != System.Text.Json.JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "artifact must be a JSON object" });
            }

            DateTime asOf = DateTime.UtcNow;
            if (artifact.TryGetProperty("as_of_utc", out var asOfEl)
                && asOfEl.ValueKind == System.Text.Json.JsonValueKind.String
                && DateTime.TryParse(asOfEl.GetString(), out var parsed))
            {
                asOf = parsed.ToUniversalTime();
            }

            var artifactJson = JsonbHelpers.ToJsonb(artifact);

            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO today_setups_results
                  (universe, label, artifact, as_of_utc, uploaded_at_utc,
                   uploaded_by, note)
                VALUES (@universe, @label, @artifactJson::jsonb,
                        @asOf, NOW(), @uploadedBy, @note)
                ON CONFLICT (universe, label) DO UPDATE
                SET artifact = EXCLUDED.artifact,
                    as_of_utc = EXCLUDED.as_of_utc,
                    uploaded_at_utc = NOW(),
                    uploaded_by = EXCLUDED.uploaded_by,
                    note = EXCLUDED.note;",
                new { universe, label, artifactJson, asOf, uploadedBy, note });

            return Results.Ok(new { accepted = true, universe, label, asOfUtc = asOf });
        });

        return app;
    }

    private sealed record SetupsRow(
        string artifact_text,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);
}
