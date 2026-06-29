using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/fill-replay/* — read API for the fill-replay artifact produced by
/// `tradepro-fill-replay` on the Mac (replays a strategy's ACTUAL OMS fills →
/// per-symbol entry-extension + outcome + one-shot-vs-staggered, split by
/// universe). The honest diagnosis surface for LIVE losses: the backtest models
/// strategy LOGIC; this measures what we ACTUALLY did (entry timing, seeding,
/// concentration). It's what refuted the over-extension hypothesis (2026-06-28).
///
/// Mac worker pushes the JSON artifact via /api/ingest/fill-replay (IngestToken
/// auth). The cockpit card polls the read endpoint to render it.
///
/// Same store shape as equity_pipeline_results: one row per (strategy, label).
/// </summary>
public static class FillReplayEndpoints
{
    /// <summary>User-facing read routes.</summary>
    public static IEndpointRouteBuilder MapFillReplayUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/fill-replay").WithTags("FillReplay");

        // GET /api/fill-replay/{strategy}/latest — most recent artifact.
        // Optional ?label= (default "latest"). 404 when no row exists →
        // the UI shows "no replay yet" + the CLI runbook.
        group.MapGet("/{strategy}/latest", async (
            string strategy, string? label, NpgsqlDataSource db) =>
        {
            var l = string.IsNullOrWhiteSpace(label) ? "latest" : label;
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<ReplayRow>(@"
                SELECT artifact::text AS artifact_text,
                       as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM fill_replay_results
                WHERE strategy = @strategy AND label = @label
                LIMIT 1;",
                new { strategy, label = l });
            if (row is null)
            {
                return Results.NotFound(new
                {
                    error = $"no fill-replay artifact for {strategy} (label={l})",
                    hint = "run `tradepro-fill-replay --push` on the worker host",
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

        // GET /api/fill-replay/{strategy} — list available labels.
        group.MapGet("/{strategy}", async (string strategy, NpgsqlDataSource db) =>
        {
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<ReplaySummaryRow>(@"
                SELECT label, as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM fill_replay_results
                WHERE strategy = @strategy
                ORDER BY as_of_utc DESC;",
                new { strategy });
            return Results.Ok(new { strategy, runs = rows });
        });

        return app;
    }

    /// <summary>Mac-pushed ingest route.</summary>
    public static IEndpointRouteBuilder MapFillReplayIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("FillReplay/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/fill-replay
        // Body: { "strategy": "...", "label": "latest", "uploaded_by": "...",
        //         "note": "...", "artifact": { ...CLI emit... } }
        // Upserts on (strategy, label) so re-runs overwrite cleanly.
        group.MapPost("/fill-replay", async (
            System.Text.Json.JsonElement payload, NpgsqlDataSource db) =>
        {
            if (payload.ValueKind != System.Text.Json.JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            var strategy = JsonbHelpers.ReadString(payload, "strategy");
            if (string.IsNullOrWhiteSpace(strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            var label = JsonbHelpers.ReadString(payload, "label") ?? "latest";
            var uploadedBy = JsonbHelpers.ReadString(payload, "uploaded_by");
            var note = JsonbHelpers.ReadString(payload, "note");

            if (!payload.TryGetProperty("artifact", out var artifact)
                || artifact.ValueKind != System.Text.Json.JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "artifact must be a JSON object" });
            }

            // CLI stamps as_of_utc inside the artifact; trust it for ordering,
            // fall back to NOW() if missing/unparseable.
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
                INSERT INTO fill_replay_results
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

            return Results.Ok(new { accepted = true, strategy, label, asOfUtc = asOf });
        });

        return app;
    }

    private sealed record ReplayRow(
        string artifact_text,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);

    private sealed record ReplaySummaryRow(
        string label,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);
}
