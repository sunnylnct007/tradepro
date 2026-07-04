using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/run-log — the CENTRAL run-log every process (Mac daemons, EC2 API, harvest,
/// audits) writes to, so operational issues surface in ONE place, loud, across
/// machines. Append via /api/ingest/run-log; the cockpit reads the recent slice and
/// highlights failures/stale. Covers ALL ops (kind) + ALL brokers (broker).
/// </summary>
public static class RunLogEndpoints
{
    public static IEndpointRouteBuilder MapRunLogUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/run-log").WithTags("RunLog");

        // GET /api/run-log/recent?limit=100&status=fail&process=...
        group.MapGet("/recent", async (
            int? limit, string? status, string? process, NpgsqlDataSource db) =>
        {
            var lim = Math.Clamp(limit ?? 100, 1, 1000);
            await using var conn = await db.OpenConnectionAsync();
            var rows = (await conn.QueryAsync(@"
                SELECT id, process, machine, kind, broker, symbol, status, error, summary,
                       started_at_utc, finished_at_utc, created_at_utc
                FROM run_log
                WHERE (@status IS NULL OR status = @status)
                  AND (@process IS NULL OR process = @process)
                ORDER BY created_at_utc DESC
                LIMIT @lim;",
                new { status, process, lim })).ToList();

            // Small health rollup so the cockpit can badge without a 2nd call:
            // counts by status over the last 24h.
            var health = (await conn.QueryAsync(@"
                SELECT status, COUNT(*) AS n
                FROM run_log
                WHERE created_at_utc > NOW() - INTERVAL '24 hours'
                GROUP BY status;")).ToDictionary(
                    r => (string)r.status, r => (long)r.n);

            return Results.Ok(new { rows, health24h = health });
        });

        return app;
    }

    public static IEndpointRouteBuilder MapRunLogIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("RunLog/Ingest")
            .RequireAuthorization(Auth.IngestTokenAuth.Policy);

        // POST /api/ingest/run-log
        // Body: a single event OR { "events": [ ... ] }. Each event:
        //   { process, machine?, kind, broker?, symbol?, status, error?, summary?,
        //     started_at_utc?, finished_at_utc? }
        group.MapPost("/run-log", async (
            System.Text.Json.JsonElement payload, NpgsqlDataSource db) =>
        {
            var events = new List<System.Text.Json.JsonElement>();
            if (payload.ValueKind == System.Text.Json.JsonValueKind.Object
                && payload.TryGetProperty("events", out var arr)
                && arr.ValueKind == System.Text.Json.JsonValueKind.Array)
            {
                foreach (var e in arr.EnumerateArray()) events.Add(e);
            }
            else if (payload.ValueKind == System.Text.Json.JsonValueKind.Object)
            {
                events.Add(payload);
            }
            else
            {
                return Results.BadRequest(new { error = "payload must be an event object or {events:[...]}" });
            }

            await using var conn = await db.OpenConnectionAsync();
            int inserted = 0;
            foreach (var e in events)
            {
                var process = JsonbHelpers.ReadString(e, "process");
                var kind = JsonbHelpers.ReadString(e, "kind");
                var status = JsonbHelpers.ReadString(e, "status");
                if (string.IsNullOrWhiteSpace(process) || string.IsNullOrWhiteSpace(kind)
                    || string.IsNullOrWhiteSpace(status))
                    continue; // skip malformed rather than fail the whole batch
                await conn.ExecuteAsync(@"
                    INSERT INTO run_log
                      (process, machine, kind, broker, symbol, status, error, summary,
                       started_at_utc, finished_at_utc)
                    VALUES (@process, @machine, @kind, @broker, @symbol, @status, @error, @summary,
                            @started, @finished);",
                    new
                    {
                        process,
                        machine = JsonbHelpers.ReadString(e, "machine"),
                        kind,
                        broker = JsonbHelpers.ReadString(e, "broker"),
                        symbol = JsonbHelpers.ReadString(e, "symbol"),
                        status,
                        error = JsonbHelpers.ReadString(e, "error"),
                        summary = JsonbHelpers.ReadString(e, "summary"),
                        started = ParseTs(e, "started_at_utc"),
                        finished = ParseTs(e, "finished_at_utc"),
                    });
                inserted++;
            }
            return Results.Ok(new { accepted = true, inserted });
        });

        return app;
    }

    private static DateTime? ParseTs(System.Text.Json.JsonElement e, string prop) =>
        e.TryGetProperty(prop, out var v)
        && v.ValueKind == System.Text.Json.JsonValueKind.String
        && DateTime.TryParse(v.GetString(), out var d)
            ? d.ToUniversalTime() : null;
}
