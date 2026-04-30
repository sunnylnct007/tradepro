using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// Read side of the Mac liveness signal. The frontend polls this on the
/// Compare page so a user can see 'Mac alive · processing etf_us_core'
/// or '● Mac down (last seen 3h ago)' — without staring at the data
/// freshness banner trying to guess.
public static class WorkerHealthEndpoints
{
    private static readonly TimeSpan AliveWindow = TimeSpan.FromMinutes(30);
    private static readonly TimeSpan LateWindow = TimeSpan.FromHours(24);

    public static IEndpointRouteBuilder MapWorkerHealthEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/health").WithTags("WorkerHealth");

        group.MapGet("/worker", (IHeartbeatStore store) =>
        {
            var hb = store.GetLatest();
            if (hb is null)
            {
                return Results.Ok(new
                {
                    liveness = WorkerLiveness.Down.ToString().ToLowerInvariant(),
                    sinceLastPingSeconds = (int?)null,
                    isProcessing = false,
                    summary = "No heartbeat yet — start the launchd job or run tradepro-heartbeat manually.",
                });
            }

            var since = DateTime.UtcNow - hb.SentAtUtc;
            var liveness =
                since <= AliveWindow ? WorkerLiveness.Alive
                : since <= LateWindow ? WorkerLiveness.Late
                : WorkerLiveness.Down;

            var isProcessing = hb.CurrentTask is not null;
            var summary = BuildSummary(hb, liveness, since, isProcessing);

            return Results.Ok(new
            {
                liveness = liveness.ToString().ToLowerInvariant(),
                sinceLastPingSeconds = (int)since.TotalSeconds,
                isProcessing,
                summary,
                host = hb.Host,
                gitSha = hb.GitSha,
                sentAtUtc = hb.SentAtUtc,
                receivedAtUtc = hb.ReceivedAtUtc,
                uptimeSeconds = hb.UptimeSeconds,
                currentTask = hb.CurrentTask is null ? null : new
                {
                    task = hb.CurrentTask,
                    detail = hb.CurrentTaskDetail,
                    phase = hb.CurrentTaskPhase,
                    startedAtUtc = hb.CurrentTaskStartedAt,
                    elapsedSeconds = hb.CurrentTaskStartedAt is null
                        ? (int?)null
                        : (int)(DateTime.UtcNow - hb.CurrentTaskStartedAt.Value).TotalSeconds,
                },
                payload = hb.Payload,
            });
        });

        return app;
    }

    private static string BuildSummary(
        HeartbeatEnvelope hb, WorkerLiveness liveness, TimeSpan since, bool isProcessing)
    {
        if (isProcessing)
        {
            var phase = hb.CurrentTaskPhase is null ? "" : $" — {hb.CurrentTaskPhase}";
            var detail = hb.CurrentTaskDetail ?? hb.CurrentTask;
            return $"Processing: {detail}{phase}";
        }

        var ago = HumaniseDuration(since);
        return liveness switch
        {
            WorkerLiveness.Alive => $"Mac alive — last ping {ago} ago, idle.",
            WorkerLiveness.Late => $"Mac late — last ping {ago} ago. May have missed a heartbeat.",
            WorkerLiveness.Down => $"Mac silent — last ping {ago} ago. Check the launchd job.",
            _ => "unknown",
        };
    }

    private static string HumaniseDuration(TimeSpan d)
    {
        if (d.TotalMinutes < 1) return "moments";
        if (d.TotalMinutes < 60) return $"{(int)d.TotalMinutes}m";
        if (d.TotalHours < 48) return $"{(int)d.TotalHours}h";
        return $"{(int)d.TotalDays}d";
    }
}
