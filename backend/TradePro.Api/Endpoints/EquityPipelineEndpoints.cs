using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/equity-pipeline/* — read API for the equity pipeline backtest
/// artifact produced by `tradepro-equity-pipeline` on the Mac
/// (full trader-spec: sleeves + ensemble + walk-forward + Monte Carlo
/// + plot data; see strategies/cli/equity_pipeline.py and the trader's
/// docs/main 4.py reference).
///
/// Mac worker pushes the JSON artifact via /api/ingest/equity-pipeline
/// (IngestToken auth). The strategy validation page polls the read
/// endpoint to render the trader's 4-panel backtest chart + MC fan.
///
/// One row per (strategy, label) — label defaults to "latest". Keeps
/// multiple labeled runs side-by-side ("with-hibeta", "2020-2025") so
/// the trader can A/B different configs without losing history.
/// </summary>
public static class EquityPipelineEndpoints
{
    /// <summary>User-facing read routes.</summary>
    public static IEndpointRouteBuilder MapEquityPipelineUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/equity-pipeline").WithTags("EquityPipeline");

        // GET /api/equity-pipeline/{strategy}/latest
        // Returns the most recent artifact for the strategy. Optional
        // ?label= picks a specific label (default "latest"). 404 when
        // no row exists — the UI shows "no pipeline run yet" and a
        // link to the CLI runbook.
        group.MapGet("/{strategy}/latest", async (
            string strategy, string? label, NpgsqlDataSource db) =>
        {
            var l = string.IsNullOrWhiteSpace(label) ? "latest" : label;
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<PipelineRow>(@"
                SELECT artifact::text AS artifact_text,
                       as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM equity_pipeline_results
                WHERE strategy = @strategy AND label = @label
                LIMIT 1;",
                new { strategy, label = l });
            if (row is null)
            {
                return Results.NotFound(new
                {
                    error = $"no equity-pipeline artifact for {strategy} (label={l})",
                    hint = "run `tradepro-equity-pipeline --push` on the worker host",
                });
            }
            return Results.Ok(new
            {
                strategy,
                label = l,
                asOfUtc = row.as_of_utc,
                uploadedAtUtc = row.uploaded_at_utc,
                uploadedBy = row.uploaded_by,
                note = row.note,
                artifact = JsonbHelpers.FromJsonb(row.artifact_text),
            });
        });

        // GET /api/equity-pipeline/{strategy} — list available labels +
        // their as_of timestamps so the UI can offer a picker if the
        // trader wants to compare runs.
        group.MapGet("/{strategy}", async (string strategy, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<PipelineSummaryRow>(@"
                SELECT label, as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM equity_pipeline_results
                WHERE strategy = @strategy
                ORDER BY as_of_utc DESC;",
                new { strategy });
            return Results.Ok(new { strategy, runs = rows });
        });

        return app;
    }

    /// <summary>Mac-pushed ingest route.</summary>
    public static IEndpointRouteBuilder MapEquityPipelineIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("EquityPipeline/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/equity-pipeline
        // Body shape:
        //   {
        //     "strategy": "ichimoku_equity",       // required
        //     "label":    "latest",                // optional, default "latest"
        //     "uploaded_by": "Sunils-MacBook.local", // optional, audit
        //     "note":     "weekly refresh",        // optional
        //     "artifact": { ...as emitted by the CLI },  // required
        //   }
        // Upserts on (strategy, label) so re-runs overwrite cleanly.
        group.MapPost("/equity-pipeline", async (
            JsonElement payload, NpgsqlDataSource db) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            var strategy = JsonbHelpers.ReadString(payload, "strategy");
            if (string.IsNullOrWhiteSpace(strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            var label = JsonbHelpers.ReadString(payload, "label") ?? "latest";
            var uploadedBy = JsonbHelpers.ReadString(payload, "uploaded_by");
            var note = JsonbHelpers.ReadString(payload, "note");

            if (!payload.TryGetProperty("artifact", out var artifact)
                || artifact.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "artifact must be a JSON object" });
            }

            // The CLI stamps as_of_utc inside the artifact. Trust it
            // for ordering; fall back to NOW() if missing/unparseable.
            DateTime asOf = DateTime.UtcNow;
            if (artifact.TryGetProperty("as_of_utc", out var asOfEl)
                && asOfEl.ValueKind == JsonValueKind.String
                && DateTime.TryParse(asOfEl.GetString(), out var parsed))
            {
                asOf = parsed.ToUniversalTime();
            }

            var artifactJson = JsonbHelpers.ToJsonb(artifact);

            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO equity_pipeline_results
                  (strategy, label, artifact, as_of_utc, uploaded_at_utc,
                   uploaded_by, note)
                VALUES (@strategy, @label, @artifactJson::jsonb,
                        @asOf, NOW(), @uploadedBy, @note)
                ON CONFLICT (strategy, label) DO UPDATE
                SET artifact = EXCLUDED.artifact,
                    as_of_utc = EXCLUDED.as_of_utc,
                    uploaded_at_utc = NOW(),
                    uploaded_by = EXCLUDED.uploaded_by,
                    note = EXCLUDED.note;",
                new { strategy, label, artifactJson, asOf, uploadedBy, note });

            return Results.Ok(new
            {
                accepted = true,
                strategy, label, asOfUtc = asOf,
            });
        });

        return app;
    }

    private sealed record PipelineRow(
        string artifact_text,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);

    private sealed record PipelineSummaryRow(
        string label,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);
}
