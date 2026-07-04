using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/signal-audit/* — read API for the "Signal vs Position" audit artifact
/// produced by `tradepro-signal-audit` on the Mac. For each strategy it reads the
/// BROKER-GOLDEN held positions and re-runs the trader's stateful Ichimoku signal
/// on each, flagging DIVERGENCES: exit-overdue (signal says SELL but still held)
/// and blind (held but no bars to evaluate). Surfaces the "exits not firing /
/// running blind" gap the rest of the UI hides.
///
/// Mac worker pushes via /api/ingest/signal-audit (IngestToken). Keyed by
/// (strategy, label) — same store pattern as today-setups / fill-replay.
/// </summary>
public static class SignalAuditEndpoints
{
    public static IEndpointRouteBuilder MapSignalAuditUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/signal-audit").WithTags("SignalAudit");

        // GET /api/signal-audit/{strategy}/latest
        group.MapGet("/{strategy}/latest", async (
            string strategy, string? label, NpgsqlDataSource db) =>
        {
            var l = string.IsNullOrWhiteSpace(label) ? "latest" : label;
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<AuditRow>(@"
                SELECT artifact::text AS artifact_text,
                       as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM signal_audit_results
                WHERE strategy = @strategy AND label = @label
                LIMIT 1;",
                new { strategy, label = l });
            if (row is null)
            {
                return Results.NotFound(new
                {
                    error = $"no signal-audit artifact for {strategy} (label={l})",
                    hint = "run `tradepro-signal-audit --push` on the worker host",
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

        return app;
    }

    public static IEndpointRouteBuilder MapSignalAuditIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("SignalAudit/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/signal-audit
        // Body: { "strategy": "ichimoku_equity", "label": "latest",
        //         "uploaded_by": "...", "note": "...", "artifact": { ...CLI emit... } }
        group.MapPost("/signal-audit", async (
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
                INSERT INTO signal_audit_results
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

    private sealed record AuditRow(
        string artifact_text,
        DateTime as_of_utc,
        DateTime uploaded_at_utc,
        string? uploaded_by,
        string? note);
}
