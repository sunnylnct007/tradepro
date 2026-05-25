using System.Text.Json;
using TradePro.Api.Auth;
using TradePro.Api.Data.Stores;
using TradePro.Api.Providers;
using TradePro.Api.Simulation;
// IIntradayLeaderboardStore lives in TradePro.Api.Data.Stores too;
// the using above covers it.

namespace TradePro.Api.Endpoints;

/// <summary>
/// UI-triggered ops queue. Task #69 step A.
///
/// Two surfaces share the same backing <see cref="ISessionRequestsStore"/>:
///
///   • User routes — POST /api/ops/run-intraday (enqueue), GET
///     /api/ops/sessions (list), POST /api/ops/sessions/{id}/cancel.
///     Firebase / AllowedUsers auth, mounted alongside the rest of /api.
///
///   • Mac routes — POST /api/ops/poll-intraday (claim one), POST
///     /api/ops/complete-intraday/{id}. IngestToken bearer auth,
///     mounted on the same /api group as /api/ingest/*.
///
/// The split mirrors the existing IngestEndpoints arrangement so the
/// Mac worker (no human, static service token) and the browser
/// (Firebase ID token) keep separate trust boundaries.
/// </summary>
public static class OpsEndpoints
{
    private const string Kind = "intraday";

    /// User-facing routes — mount on the AllowedUsers /api group.
    public static IEndpointRouteBuilder MapOpsUserEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ops").WithTags("Ops");

        // Queue an intraday session. Body is the param payload that
        // the Mac will see when it claims the row — symbols, window,
        // gate thresholds, etc. Step C will define the schema; for
        // now we accept any JSON object and pass it through.
        group.MapPost("/run-intraday", (JsonElement payload, ISessionRequestsStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var req = store.Put(Kind, payload);
            return Results.Ok(Envelope(req));
        });

        // Read the queue (Pending + recent terminal rows). `kind` is
        // optional so the same endpoint surfaces other ops kinds
        // later (compare, backtest) once they exist.
        group.MapGet("/sessions", (string? kind, int? limit, ISessionRequestsStore store) =>
        {
            var rows = store.List(kind, limit ?? 100);
            return Results.Ok(new { sessions = rows.Select(Envelope).ToArray() });
        });

        // Cancel a still-Pending row. Once Claimed the work is on the
        // Mac and cancel becomes a no-op — the row stays in Claimed
        // until Mac reports back.
        group.MapPost("/sessions/{requestId}/cancel", (string requestId, ISessionRequestsStore store) =>
        {
            var req = store.Cancel(requestId);
            return req is null
                ? Results.NotFound(new { error = $"no session with id {requestId}" })
                : Results.Ok(Envelope(req));
        });

        // Per-(symbol, strategy) leaderboard rolled up over every
        // completed intraday session. Answers "if I'd used strategy X
        // on symbol Y, would it have made money?" from the data the
        // engine has already been writing into session_requests.
        group.MapGet("/leaderboard", (IIntradayLeaderboardStore store) =>
            Results.Ok(store.Build()));

        // Queue a paper-trading session. Params: strategy, symbols (array),
        // capital_usd, broker, placement_mode, interval. Mac daemon polls
        // /ops/poll-paper to claim the row and run tradepro-paper.
        group.MapPost("/run-paper", (JsonElement payload, ISessionRequestsStore store, SqsTriggerService sqsTrigger) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            var req = store.Put("paper_session", payload);
            sqsTrigger.SendTrigger(req.RequestId, payload);  // fire-and-forget
            return Results.Ok(Envelope(req));
        });

        // List paper-session requests (pending + recent terminal).
        group.MapGet("/paper-sessions", (int? limit, ISessionRequestsStore store) =>
        {
            var rows = store.List("paper_session", limit ?? 50);
            return Results.Ok(new { sessions = rows.Select(Envelope).ToArray() });
        });

        // Cancel a pending paper-session request.
        group.MapPost("/paper-sessions/{requestId}/cancel", (string requestId, ISessionRequestsStore store) =>
        {
            var req = store.Cancel(requestId);
            return req is null
                ? Results.NotFound(new { error = $"no session with id {requestId}" })
                : Results.Ok(Envelope(req));
        });

        return app;
    }

    /// Mac worker routes — mount on the IngestToken /api group.
    public static IEndpointRouteBuilder MapOpsWorkerEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ops")
            .WithTags("OpsWorker")
            .RequireAuthorization(IngestTokenAuth.Policy);

        // Mac polls this on a cadence (cron / launchd / loop) to pick
        // up one pending request. Atomic claim via UPDATE-RETURNING
        // with FOR UPDATE SKIP LOCKED so two parallel pollers don't
        // both grab the same row.
        group.MapPost("/poll-intraday", (JsonElement payload, ISessionRequestsStore store) =>
        {
            var host = ReadStringOrDefault(payload, "host", "mac");
            var req = store.Claim(Kind, host);
            return req is null
                ? Results.Ok(new { claimed = false })
                : Results.Ok(new { claimed = true, session = Envelope(req) });
        });

        // Mac posts the result back. `status` is "completed" or
        // "failed"; `result_summary` is op-specific (e.g. orders
        // placed, P&L, errors-per-symbol).
        group.MapPost("/complete-intraday/{requestId}", (string requestId, JsonElement payload, ISessionRequestsStore store) =>
        {
            var status = ReadStringOrDefault(payload, "status", "completed").ToLowerInvariant();
            if (status == "failed")
            {
                var error = ReadStringOrDefault(payload, "error", "unspecified failure");
                var req = store.MarkFailed(requestId, error);
                return req is null
                    ? Results.NotFound(new { error = $"no session with id {requestId}" })
                    : Results.Ok(Envelope(req));
            }

            JsonElement? summary = null;
            if (payload.ValueKind == JsonValueKind.Object
                && payload.TryGetProperty("result_summary", out var s))
            {
                summary = s;
            }
            var done = store.MarkCompleted(requestId, summary);
            return done is null
                ? Results.NotFound(new { error = $"no session with id {requestId}" })
                : Results.Ok(Envelope(done));
        });

        // Mac daemon polls this for pending paper-session requests.
        group.MapPost("/poll-paper", (JsonElement payload, ISessionRequestsStore store) =>
        {
            var host = ReadStringOrDefault(payload, "host", "mac");
            var req = store.Claim("paper_session", host);
            return req is null
                ? Results.Ok(new { claimed = false })
                : Results.Ok(new { claimed = true, session = Envelope(req) });
        });

        // Mac reports completion (or failure) of a paper-session request.
        group.MapPost("/complete-paper/{requestId}", (string requestId, JsonElement payload, ISessionRequestsStore store) =>
        {
            var status = ReadStringOrDefault(payload, "status", "completed").ToLowerInvariant();
            if (status == "failed")
            {
                var error = ReadStringOrDefault(payload, "error", "unspecified failure");
                var req = store.MarkFailed(requestId, error);
                return req is null
                    ? Results.NotFound(new { error = $"no session with id {requestId}" })
                    : Results.Ok(Envelope(req));
            }
            JsonElement? summary = null;
            if (payload.ValueKind == JsonValueKind.Object
                && payload.TryGetProperty("result_summary", out var s))
            {
                summary = s;
            }
            var done = store.MarkCompleted(requestId, summary);
            return done is null
                ? Results.NotFound(new { error = $"no session with id {requestId}" })
                : Results.Ok(Envelope(done));
        });

        return app;
    }

    private static object Envelope(SessionRequest r) => new
    {
        request_id = r.RequestId,
        kind = r.Kind,
        @params = r.Params,
        state = r.State.ToString(),
        requested_at_utc = r.RequestedAtUtc,
        claimed_at_utc = r.ClaimedAtUtc,
        claimed_by = r.ClaimedBy,
        completed_at_utc = r.CompletedAtUtc,
        result_summary = r.ResultSummary,
        error = r.Error,
    };

    private static string ReadStringOrDefault(JsonElement el, string key, string fallback)
        => JsonbHelpers.ReadString(el, key) ?? fallback;
}
