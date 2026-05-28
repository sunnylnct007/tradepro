using System.Text.Json;
using TradePro.Api.Auth;
using TradePro.Api.Data.Stores;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/quant — quant-engine backtest trigger queue. Mirrors the
/// paper-session split in <see cref="OpsEndpoints"/>:
///
///   • User routes   (POST /api/quant/backtest/run, GET /api/quant/backtest/{id},
///                    GET /api/quant/backtest/) — Firebase auth, mounted under
///                    the AllowedUsers /api group.
///   • Worker routes (POST /api/ops/poll-backtest, POST /api/ops/complete-backtest/{id})
///                    — IngestToken auth, share the /ops group with the
///                    paper-session daemon so a single launchd agent
///                    services both queues.
///
/// State machine + storage are the same generic session_requests row;
/// the kind discriminator is hardcoded to "backtest" here. The CLI
/// half lives in strategies/tradepro_strategies/cli/quant_backtest.py.
/// </summary>
public static class QuantEndpoints
{
    public const string Kind = "backtest";

    /// <summary>Inbound payload contract for /api/quant/backtest/run.
    /// All fields optional except <see cref="Strategy"/> and
    /// <see cref="Symbols"/> — the worker CLI rejects an empty symbol
    /// list, so we mirror that contract at the API surface for a
    /// fast-fail rather than a slow-fail.</summary>
    public sealed record BacktestRequest(
        string Strategy,
        string[] Symbols,
        string? Start,
        string? End,
        double? InitialCapital,
        int? NSims,
        int? Years,
        int? Seed,
        string? Label);

    /// User-facing routes. Mount on the AllowedUsers /api group.
    public static IEndpointRouteBuilder MapQuantEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/quant/backtest").WithTags("Quant");

        // POST /api/quant/backtest/run — enqueue a new backtest. Body
        // is the BacktestRequest record; we normalise it into the
        // worker's JSON payload schema and store under session_requests.
        group.MapPost("/run", (BacktestRequest req, ISessionRequestsStore store) =>
        {
            if (string.IsNullOrWhiteSpace(req.Strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            if (req.Symbols is null || req.Symbols.Length == 0)
                return Results.BadRequest(new { error = "symbols must be a non-empty list" });

            var payload = BuildWorkerPayload(req);
            var row = store.Put(Kind, payload);
            return Results.Ok(new { requestId = row.RequestId, state = row.State.ToString() });
        });

        // GET /api/quant/backtest/{id} — single-row lookup. Mirrors
        // /api/ops/sessions/{id}; the frontend hits this on the
        // Backtests detail page to render charts once ready.
        group.MapGet("/{requestId}", (string requestId, ISessionRequestsStore store) =>
        {
            var row = store.Get(requestId);
            return row is null
                ? Results.NotFound(new { error = $"no backtest with id {requestId}" })
                : Results.Ok(Envelope(row));
        });

        // GET /api/quant/backtest/ — recent runs newest-first. Filter
        // pre-applied to kind="backtest" so the page doesn't fish
        // through paper / intraday rows.
        group.MapGet("/", (int? limit, ISessionRequestsStore store) =>
        {
            var rows = store.List(Kind, limit ?? 50);
            return Results.Ok(new { backtests = rows.Select(Envelope).ToArray() });
        });

        // POST /api/quant/backtest/{id}/cancel — only meaningful while
        // Pending; the worker is fire-and-forget once Claimed.
        group.MapPost("/{requestId}/cancel", (string requestId, ISessionRequestsStore store) =>
        {
            var row = store.Cancel(requestId);
            return row is null
                ? Results.NotFound(new { error = $"no backtest with id {requestId}" })
                : Results.Ok(Envelope(row));
        });

        return app;
    }

    /// Worker-facing routes. Mount on the IngestToken /api group.
    /// Lives under /ops so the daemon's existing one-poller-per-tick
    /// loop can reach it alongside /ops/poll-paper.
    public static IEndpointRouteBuilder MapQuantWorkerEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ops")
            .WithTags("QuantWorker")
            .RequireAuthorization(IngestTokenAuth.Policy);

        // POST /api/ops/poll-backtest — Mac daemon claims one pending
        // backtest. FOR UPDATE SKIP LOCKED in the store guarantees
        // two pollers can't race the same row.
        group.MapPost("/poll-backtest", (JsonElement payload, ISessionRequestsStore store) =>
        {
            var host = JsonbHelpers.ReadString(payload, "host") ?? "mac";
            var row = store.Claim(Kind, host);
            return row is null
                ? Results.Ok(new { claimed = false })
                : Results.Ok(new { claimed = true, session = Envelope(row) });
        });

        // POST /api/ops/complete-backtest/{id} — Mac posts the
        // result_summary (or failure). On status="completed" we expect
        // the full backtest envelope (charts + summary + strategies);
        // on "failed" only the error string is required.
        group.MapPost("/complete-backtest/{requestId}",
            (string requestId, JsonElement payload, ISessionRequestsStore store) =>
        {
            var status = (JsonbHelpers.ReadString(payload, "status") ?? "completed").ToLowerInvariant();
            if (status == "failed")
            {
                var error = JsonbHelpers.ReadString(payload, "error") ?? "unspecified failure";
                var row = store.MarkFailed(requestId, error);
                return row is null
                    ? Results.NotFound(new { error = $"no backtest with id {requestId}" })
                    : Results.Ok(Envelope(row));
            }

            JsonElement? summary = null;
            if (payload.ValueKind == JsonValueKind.Object
                && payload.TryGetProperty("result_summary", out var s))
            {
                summary = s;
            }
            var done = store.MarkCompleted(requestId, summary);
            return done is null
                ? Results.NotFound(new { error = $"no backtest with id {requestId}" })
                : Results.Ok(Envelope(done));
        });

        return app;
    }

    /// Translate the typed BacktestRequest into the loosely-typed
    /// JSON payload the worker CLI consumes. Centralised here so the
    /// API ↔ worker contract stays in lockstep.
    private static JsonElement BuildWorkerPayload(BacktestRequest req)
    {
        var mc = new Dictionary<string, object?>
        {
            ["n_sims"] = req.NSims ?? 500,
            ["years"] = req.Years ?? 5,
        };
        if (req.Seed.HasValue) mc["seed"] = req.Seed.Value;

        var dict = new Dictionary<string, object?>
        {
            ["kind"] = Kind,
            ["strategy"] = req.Strategy,
            ["symbols"] = req.Symbols,
            ["start"] = req.Start ?? "2020-01-01",
            ["end"] = req.End ?? DateTime.UtcNow.ToString("yyyy-MM-dd"),
            ["initial_capital"] = req.InitialCapital ?? 100_000.0,
            ["monte_carlo"] = mc,
            ["label"] = req.Label ?? req.Strategy,
        };
        var json = JsonSerializer.Serialize(dict);
        return JsonDocument.Parse(json).RootElement.Clone();
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
}
