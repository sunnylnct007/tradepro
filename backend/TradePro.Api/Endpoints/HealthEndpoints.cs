using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

public static class HealthEndpoints
{
    public static IEndpointRouteBuilder MapHealthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/health", () => Results.Ok(new
        {
            status = "ok",
            service = "tradepro-api",
            utc = DateTime.UtcNow
        }));

        // Friendly index at the root — without it, opening
        // http://localhost:5080/ returns a bare 404 and a confused
        // user thinks the API is down. Lists the things they probably
        // wanted instead and the count of compare payloads currently
        // cached on disk so they can spot a freshness issue at a glance.
        app.MapGet("/", (ICompareStore store, IHostEnvironment env) =>
        {
            var summaries = store.ListUniverses();
            return Results.Ok(new
            {
                service = "tradepro-api",
                utc = DateTime.UtcNow,
                environment = env.EnvironmentName,
                links = new
                {
                    health = "/health",
                    health_details = "/health/details",
                    swagger = env.IsDevelopment() ? "/swagger" : null,
                    compare_universes = "/api/compare/universes",
                    compare_latest = "/api/compare/latest?universe=etf_us_core",
                },
                compare_cache = new
                {
                    universes = summaries.Count,
                    items = summaries,
                },
            });
        });

        // Single 'is the system OK?' view — combines API liveness, the
        // compare cache state, and the Mac heartbeat into one payload
        // the Health page can render. Public (no auth) so a user with a
        // broken dev login can still see what's wrong.
        app.MapGet("/health/details",
            async (
                ICompareStore compareStore,
                IHeartbeatStore heartbeatStore,
                IHostEnvironment env,
                NpgsqlDataSource db,
                CancellationToken ct) =>
        {
            var summaries = compareStore.ListUniverses();
            var hb = heartbeatStore.GetLatest();

            // Per-universe freshness: green <24h, amber 24-72h, red >72h.
            var freshness = summaries
                .Select(s =>
                {
                    var age = DateTime.UtcNow - s.GeneratedAtUtc;
                    var tone =
                        age.TotalHours < 24 ? "fresh"
                        : age.TotalHours < 72 ? "stale"
                        : "very_stale";
                    return new
                    {
                        universe = s.Universe,
                        runId = s.RunId,
                        ageHours = (int)age.TotalHours,
                        rowCount = s.RowCount,
                        rankMetric = s.RankMetric,
                        tone,
                        generatedAtUtc = s.GeneratedAtUtc,
                    };
                })
                .ToArray();

            string workerLiveness = "down";
            int? sinceLastPing = null;
            if (hb is not null)
            {
                var since = DateTime.UtcNow - hb.SentAtUtc;
                sinceLastPing = (int)since.TotalSeconds;
                workerLiveness =
                    since.TotalMinutes <= 30 ? "alive"
                    : since.TotalHours <= 24 ? "late"
                    : "down";
            }

            // Coarse 'is anything red' verdict for the badge at the top
            // of the Health page.
            var anyVeryStale = freshness.Any(f => f.tone == "very_stale");
            var verdict =
                workerLiveness == "down" || anyVeryStale ? "needs_attention"
                : workerLiveness == "late" || freshness.Any(f => f.tone == "stale") ? "warn"
                : "ok";

            // Postgres liveness ping — round-trip a trivial query so a
            // dead/migrating DB shows up red on the Health page rather
            // than as a confusing "all green" + 500s on every other
            // endpoint. Measured in ms so latency drift is visible.
            var pgUp = false;
            long? pgLatencyMs = null;
            string? pgError = null;
            try
            {
                var sw = System.Diagnostics.Stopwatch.StartNew();
                await using var conn = await db.OpenConnectionAsync(ct);
                _ = await conn.ExecuteScalarAsync<int>("SELECT 1;");
                sw.Stop();
                pgUp = true;
                pgLatencyMs = sw.ElapsedMilliseconds;
            }
            catch (Exception ex)
            {
                pgError = ex.Message;
            }

            var apiUptimeSeconds = (int)(DateTime.UtcNow
                - System.Diagnostics.Process.GetCurrentProcess().StartTime.ToUniversalTime()
            ).TotalSeconds;

            return Results.Ok(new
            {
                verdict,
                utc = DateTime.UtcNow,
                environment = env.EnvironmentName,
                gitSha = Environment.GetEnvironmentVariable("GIT_SHA")
                    ?? Environment.GetEnvironmentVariable("GITHUB_SHA")
                    ?? "unknown",
                // Deployment provenance — set during Docker build via
                // --build-arg (CI workflow). Lets the operator confirm
                // "is the live container the commit I just pushed?"
                // without SSM-ing into the host.
                deploy = new
                {
                    backendCommit = Environment.GetEnvironmentVariable("BACKEND_COMMIT") ?? "unknown",
                    backendBuildTime = Environment.GetEnvironmentVariable("BACKEND_BUILD_TIME") ?? "unknown",
                    apiUptimeSeconds,
                    postgres = new
                    {
                        connected = pgUp,
                        latencyMs = pgLatencyMs,
                        error = pgError,
                    },
                },
                api = new
                {
                    status = "ok",
                    uptimeSeconds = apiUptimeSeconds,
                },
                worker = new
                {
                    liveness = workerLiveness,
                    sinceLastPingSeconds = sinceLastPing,
                    host = hb?.Host,
                    isProcessing = hb?.CurrentTask is not null,
                    currentTask = hb?.CurrentTask is null ? null : new
                    {
                        task = hb.CurrentTask,
                        detail = hb.CurrentTaskDetail,
                        phase = hb.CurrentTaskPhase,
                    },
                },
                compareCache = new
                {
                    universes = freshness.Length,
                    freshness,
                },
            });
        });

        return app;
    }
}
