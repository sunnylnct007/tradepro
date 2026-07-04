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
    // Processes that SHOULD run on a cadence — if the last run is older than MaxAgeHours
    // the process is STALE/dead (the absence the cockpit flags loud). Daily jobs get ~30h
    // (a day + slack + the overnight box stop); the 15-min paper daemons get a few hours.
    // Add a process here the moment it starts writing run_log heartbeats.
    private static readonly (string Process, double MaxAgeHours)[] _expected = new[]
    {
        ("bar-cache-harvest", 30.0),
        ("live-portfolio", 30.0),   // the uploader that was dead 5 weeks — now watched
        ("signal-audit", 30.0),
        ("today-setups", 30.0),
        // TODO: add the paper-* session daemons once they write run_log heartbeats.
    };

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

            // ABSENCE DETECTION — a process that STOPPED writes nothing, so failure
            // rollups can't see it. Check each EXPECTED process's last run against its
            // cadence; anything overdue is STALE/dead and gets flagged loud. (The
            // strategy_decisions uploader was dead 5 weeks with no signal — this is
            // the class of failure this catches.)
            var lastRuns = (await conn.QueryAsync(@"
                SELECT process, MAX(created_at_utc) AS last_run
                FROM run_log GROUP BY process;"))
                .ToDictionary(r => (string)r.process, r => (DateTime)r.last_run);

            var processes = _expected.Select(e =>
            {
                lastRuns.TryGetValue(e.Process, out var last);
                var has = last != default;
                var ageH = has ? (DateTime.UtcNow - last).TotalHours : (double?)null;
                var stale = !has || (ageH ?? double.MaxValue) > e.MaxAgeHours;
                return new
                {
                    process = e.Process,
                    lastRunUtc = has ? last : (DateTime?)null,
                    ageHours = ageH,
                    maxAgeHours = e.MaxAgeHours,
                    stale,
                };
            }).ToList();

            return Results.Ok(new { rows, health24h = health, processes });
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
