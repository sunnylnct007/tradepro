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

        // Single-session lookup. Backs the Session Detail page so it can
        // render the full snapshot (bars_seen, decisions, fills, positions)
        // even when navigated to directly via URL.
        group.MapGet("/sessions/{requestId}", (string requestId, ISessionRequestsStore store) =>
        {
            var req = store.Get(requestId);
            return req is null
                ? Results.NotFound(new { error = $"no session with id {requestId}" })
                : Results.Ok(Envelope(req));
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

        // ─── Data ops (Phase C-Validate slice) ──────────────────────
        // Trustworthy data layer's UI-triggerable ops surface. Reuses
        // session_requests + the same Claim/MarkCompleted/MarkFailed
        // primitives the intraday + paper queues use. ROADMAP
        // "Trustworthy data layer" / "Operational model" documents the
        // 5 op kinds (data_validate / data_backfill / data_reload /
        // data_repartition / data_purge); this PR lands only
        // data_validate so the queue + worker pattern is proven on a
        // non-destructive op first.
        //
        // Payload schemas (per the ROADMAP table):
        //   data_validate    { canonical, asset_class, resolution? }
        //
        // The Mac-side `tradepro-data-worker` daemon polls
        // /api/ops/poll-data, walks the bar cache for the requested
        // (canonical, asset_class), and posts the gap report back via
        // /complete-data/{requestId}.

        const string DataValidateKind = "data_validate";
        const string DataBackfillKind = "data_backfill";

        group.MapPost("/run-data-validate", (JsonElement payload, ISessionRequestsStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            // Minimal validation so the worker gets something usable.
            if (!payload.TryGetProperty("canonical", out var canonical)
                || canonical.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(canonical.GetString()))
                return Results.BadRequest(new { error = "canonical required" });
            if (!payload.TryGetProperty("asset_class", out var assetClass)
                || assetClass.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(assetClass.GetString()))
                return Results.BadRequest(new { error = "asset_class required" });
            var req = store.Put(DataValidateKind, payload);
            return Results.Ok(Envelope(req));
        });

        // ─── Phase C-Backfill: enqueue a data_backfill op ───────────
        // Requires more fields than validate — the worker calls
        // BarStore.get(canonical, asset_class, resolution, start, end)
        // which is meaningless without all four.
        //
        // Date format validation is intentionally cheap (parse + reject)
        // rather than business-rule (e.g. "from must be a trading day").
        // The worker enforces real semantics; here we just keep
        // unprocessable junk out of the queue.
        group.MapPost("/run-data-backfill", (JsonElement payload, ISessionRequestsStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            if (!TryReadNonEmptyString(payload, "canonical", out var canonical))
                return Results.BadRequest(new { error = "canonical required" });
            if (!TryReadNonEmptyString(payload, "asset_class", out var assetClass))
                return Results.BadRequest(new { error = "asset_class required" });
            if (!TryReadNonEmptyString(payload, "resolution", out var resolution))
                return Results.BadRequest(new { error = "resolution required (e.g. '1m', '1d')" });
            if (!TryReadNonEmptyString(payload, "from", out var fromDate))
                return Results.BadRequest(new { error = "from required (YYYY-MM-DD)" });
            if (!IsValidDateOrToday(fromDate))
                return Results.BadRequest(new { error = "from must be YYYY-MM-DD or 'today'" });
            if (payload.TryGetProperty("to", out var toEl)
                && toEl.ValueKind == JsonValueKind.String
                && !string.IsNullOrWhiteSpace(toEl.GetString())
                && !IsValidDateOrToday(toEl.GetString()!))
            {
                return Results.BadRequest(new { error = "to must be YYYY-MM-DD or 'today'" });
            }
            var req = store.Put(DataBackfillKind, payload);
            return Results.Ok(Envelope(req));
        });

        // The worker daemon polls this; multi-kind polling (one daemon
        // handles all data_* kinds when they exist) is the natural
        // extension. For now it's data_validate only — adding kinds
        // is purely additive on the Python side.
        group.MapPost("/poll-data", (JsonElement payload, ISessionRequestsStore store) =>
        {
            var host = ReadStringOrDefault(payload, "host", "mac");
            // Accept an optional `kinds` array to future-proof when
            // the worker handles more than one data_* kind. Default
            // to data_validate which is all we ship today.
            var kinds = new List<string>();
            if (payload.ValueKind == JsonValueKind.Object
                && payload.TryGetProperty("kinds", out var k)
                && k.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in k.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.String)
                    {
                        var s = item.GetString();
                        if (!string.IsNullOrWhiteSpace(s)) kinds.Add(s);
                    }
                }
            }
            if (kinds.Count == 0) kinds.Add(DataValidateKind);

            // Try each kind in order; first available row is claimed.
            // Phase D upgrades to a single-query OR claim when the
            // store gains a multi-kind helper; for now N round-trips
            // is fine — at small N + idle queue most don't return rows.
            foreach (var kind in kinds)
            {
                var req = store.Claim(kind, host);
                if (req is not null)
                    return Results.Ok(new { claimed = true, session = Envelope(req) });
            }
            return Results.Ok(new { claimed = false });
        });

        // Worker reports completion / failure. Same shape as
        // /complete-intraday + /complete-paper for consistency.
        group.MapPost("/complete-data/{requestId}", (string requestId, JsonElement payload, ISessionRequestsStore store) =>
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

    // ── Light-touch payload validation helpers (Phase C-Backfill) ──
    // Keep enqueue-time checks cheap — the worker enforces business
    // semantics (date is a trading day, range fits provider depth,
    // etc.). The API just refuses payloads that would crash the
    // worker before it gets to handle them.

    private static bool TryReadNonEmptyString(JsonElement el, string key, out string value)
    {
        if (el.TryGetProperty(key, out var prop)
            && prop.ValueKind == JsonValueKind.String)
        {
            var s = prop.GetString();
            if (!string.IsNullOrWhiteSpace(s))
            {
                value = s;
                return true;
            }
        }
        value = string.Empty;
        return false;
    }

    private static bool IsValidDateOrToday(string s)
    {
        if (string.Equals(s, "today", StringComparison.OrdinalIgnoreCase))
            return true;
        return DateTime.TryParseExact(
            s, "yyyy-MM-dd",
            System.Globalization.CultureInfo.InvariantCulture,
            System.Globalization.DateTimeStyles.None,
            out _);
    }
}
