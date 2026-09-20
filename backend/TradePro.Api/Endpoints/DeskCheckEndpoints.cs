using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// The desk-check verdict — is the desk actually working right now.
///
/// Owner, 20 Sep 2026: "we shd be highlighting that on our dashboard if we are
/// not able to action certian things so we can fix it. observability and
/// diagnostic is key."
///
/// tradepro-desk-check already computed the right answer and could only print
/// or mail it. The first time it was run by hand it reported that the swing
/// sleeve had opened 12 positions and closed NONE — 47 exit attempts, zero
/// reaching the broker — and no screen anywhere said so. This is that verdict,
/// stored once and served to the cockpit.
///
/// Deliberately a copy of the SignalAudit shape rather than a new idea: one
/// pattern for "a CLI emits a blob, the desk renders it".
/// </summary>
public static class DeskCheckEndpoints
{
    private sealed class Row
    {
        public string artifact_text { get; set; } = "{}";
        public DateTime as_of_utc { get; set; }
        public DateTime uploaded_at_utc { get; set; }
        public string? uploaded_by { get; set; }
        public string? note { get; set; }
    }

    public static IEndpointRouteBuilder MapDeskCheckUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/desk-check").WithTags("DeskCheck");

        // GET /api/desk-check/latest
        group.MapGet("/latest", async (string? label, NpgsqlDataSource db) =>
        {
            var l = string.IsNullOrWhiteSpace(label) ? "latest" : label;
            await using var conn = await db.OpenConnectionAsync();
            var row = await conn.QueryFirstOrDefaultAsync<Row>(@"
                SELECT artifact::text AS artifact_text,
                       as_of_utc, uploaded_at_utc, uploaded_by, note
                FROM desk_check_results
                WHERE label = @label
                LIMIT 1;", new { label = l });

            // 200 WITH A STATED ABSENCE, not 404. The banner asks this on every
            // page load; a 404 renders as a failed fetch and the cockpit would
            // show nothing at all — indistinguishable from "all clear", which
            // is the exact confusion this endpoint exists to remove.
            if (row is null)
            {
                return Results.Ok(new
                {
                    label = l,
                    present = false,
                    verdict = (string?)null,
                    reason = "no desk-check has been published yet",
                    hint = "the 21:45 job runs `tradepro-desk-check --always-mail --push`",
                    artifact = (object?)null,
                });
            }
            return Results.Ok(new
            {
                label = l,
                present = true,
                asOfUtc = row.as_of_utc,
                uploadedAtUtc = row.uploaded_at_utc,
                uploadedBy = row.uploaded_by,
                note = row.note,
                artifact = JsonbHelpers.FromJsonb(row.artifact_text),
            });
        });

        return app;
    }

    public static IEndpointRouteBuilder MapDeskCheckIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("DeskCheck/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/desk-check
        // Body: { "label": "latest", "uploaded_by": "...", "artifact": {...} }
        group.MapPost("/desk-check", async (
            System.Text.Json.JsonElement payload, NpgsqlDataSource db) =>
        {
            if (payload.ValueKind != System.Text.Json.JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });

            var label = JsonbHelpers.ReadString(payload, "label") ?? "latest";
            var uploadedBy = JsonbHelpers.ReadString(payload, "uploaded_by");
            var note = JsonbHelpers.ReadString(payload, "note");

            if (!payload.TryGetProperty("artifact", out var artifact)
                || artifact.ValueKind != System.Text.Json.JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "artifact must be a JSON object" });
            }

            DateTime asOf = DateTime.UtcNow;
            if (artifact.TryGetProperty("as_of_utc", out var a)
                && a.ValueKind == System.Text.Json.JsonValueKind.String
                && DateTime.TryParse(a.GetString(), out var parsed))
            {
                asOf = parsed.ToUniversalTime();
            }

            await using var conn = await db.OpenConnectionAsync();
            await conn.ExecuteAsync(@"
                INSERT INTO desk_check_results
                  (label, artifact, as_of_utc, uploaded_at_utc, uploaded_by, note)
                VALUES (@label, @artifact::jsonb, @asOf, NOW(), @uploadedBy, @note)
                ON CONFLICT (label) DO UPDATE SET
                  artifact        = EXCLUDED.artifact,
                  as_of_utc       = EXCLUDED.as_of_utc,
                  uploaded_at_utc = EXCLUDED.uploaded_at_utc,
                  uploaded_by     = EXCLUDED.uploaded_by,
                  note            = EXCLUDED.note;",
                new { label, artifact = JsonbHelpers.ToJsonb(artifact),
                      asOf, uploadedBy, note });

            return Results.Ok(new { ok = true, label, asOfUtc = asOf });
        });

        return app;
    }
}
