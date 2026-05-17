using System.Text.Json;
using TradePro.Api.Auth;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// Receives result payloads pushed from the local Mac (`tradepro-push`).
/// Auth: `Authorization: Bearer <Ingest:Token>` — separate from the
/// Firebase login the frontend uses, because there's no human at the Mac.
public static class IngestEndpoints
{
    public static IEndpointRouteBuilder MapIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("Ingest")
            .RequireAuthorization(IngestTokenAuth.Policy);

        // The Python tradepro-push expects the POST to succeed with any 2xx
        // and prints HTTP/text on failure. Return a small JSON ack so logs
        // are useful on both ends.
        group.MapPost("/compare", (JsonElement payload, ICompareStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var env = store.Put(payload);
            return Results.Ok(new
            {
                accepted = true,
                universe = env.Universe,
                runId = env.RunId,
                rowCount = env.RowCount,
                generatedAtUtc = env.GeneratedAtUtc,
                receivedAtUtc = env.ReceivedAtUtc,
            });
        });

        // Mac → API liveness ping. Cheap (~1 KB), called every 15 min by
        // launchd and opportunistically at the start + end of each
        // tradepro-compare run so the UI sees state changes in real time.
        group.MapPost("/heartbeat", (JsonElement payload, IHeartbeatStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var env = store.Put(payload);
            return Results.Ok(new
            {
                accepted = true,
                host = env.Host,
                receivedAtUtc = env.ReceivedAtUtc,
                currentTask = env.CurrentTask,
            });
        });

        // Paper-trading backtest report (single-strategy walk-forward
        // OR multi-strategy comparator). The Mac runs the validator/
        // comparator locally and pushes the JSON; UI reads via
        // /api/paper/backtest/reports. In-memory store today —
        // restart clears history, which is fine for "show me my last
        // N backtests" while the feature is brand-new.
        group.MapPost("/paper-backtest", (JsonElement payload, IPaperBacktestStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var env = store.Put(payload);
            return Results.Ok(new
            {
                accepted = true,
                reportId = env.ReportId,
                kind = env.Kind,
                symbol = env.Symbol,
                entryCount = env.EntryCount,
                receivedAtUtc = env.ReceivedAtUtc,
            });
        });

        // Paper-trading SESSION SNAPSHOT — pushed at the end of every
        // `tradepro-paper --push` run. Carries per-strategy open
        // positions + recent fills. The Live tab on the Paper page
        // reads these to show "what just happened" without a separate
        // backtest-report flow. Snapshots are keyed by session_label
        // (typically "<symbol>-<date>") so a re-run overwrites.
        group.MapPost("/paper-snapshot", (JsonElement payload, IPaperSnapshotStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var env = store.Put(payload);
            return Results.Ok(new
            {
                accepted = true,
                sessionLabel = env.SessionLabel,
                broker = env.Broker,
                strategyCount = env.StrategyCount,
                totalFills = env.TotalFills,
                receivedAtUtc = env.ReceivedAtUtc,
            });
        });

        // Paper-trading strategies catalog — Mac introspects its registry
        // and pushes the list so the UI can show "what's available".
        // One-slot store: new push overwrites prior. Run once per deploy
        // (or whenever a new @register_strategy class lands).
        group.MapPost("/paper-strategies", (JsonElement payload, IPaperStrategiesStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            store.Put(payload);
            var count = payload.TryGetProperty("count", out var c) ? c.GetInt32() : 0;
            return Results.Ok(new { accepted = true, count, receivedAtUtc = DateTime.UtcNow });
        });

        // Document upload — Mac extracts the file (PDF / HTML / TXT) and
        // pushes the structured manifest. Raw files stay on the Mac;
        // only the extracted text + structural metadata ship to the API.
        group.MapPost("/document", (JsonElement payload, IDocumentStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var env = store.Put(payload);
            return Results.Ok(new
            {
                accepted = true,
                docId = env.DocId,
                title = env.Title,
                charCount = env.CharCount,
                linkedSymbols = env.LinkedSymbols,
                receivedAtUtc = env.ReceivedAtUtc,
            });
        });

        return app;
    }
}
