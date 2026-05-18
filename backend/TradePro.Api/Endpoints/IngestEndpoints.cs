using System.Text.Json;
using TradePro.Api.Auth;
using TradePro.Api.Data.Stores;
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

        // Paper-trading PENDING ORDER — Mac pushes this when running
        // in --placement-mode manual. We hold the order in a queue and
        // surface it on the Paper page for human Approve/Reject; the
        // human click is what triggers the actual T212 placement
        // (done by /api/paper/pending-orders/{id}/approve via the
        // .NET Trading212Client — no Mac round-trip needed).
        group.MapPost("/paper-pending-order",
            (JsonElement payload, IPendingOrdersStore store, OrdersRepository ordersRepo, ILoggerFactory lf) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            // 1) Existing projection: pending_orders gets the row that
            //    the UI's queue page reads from.
            var order = store.Put(payload);

            // 2) New: also write to the event-sourced orders table.
            //    Order id matches the pending_orders id so the two
            //    rows are joinable.
            //    Tag-along event_emitted goes onto the events log
            //    inside the repo's transaction.
            //
            //    If the orders write fails we DO NOT roll back the
            //    pending_orders insert — the pending queue is the
            //    user's actionable view, and dropping intent would
            //    be the worse failure. Log it loudly instead.
            try
            {
                ordersRepo.Insert(new NewOrder(
                    OrderId: order.OrderId,
                    StrategyName: order.StrategyId,
                    StrategyVersion: "unversioned",
                    ParamsHash: "",
                    Mode: "paper_manual",
                    Broker: order.Broker,
                    Symbol: order.Symbol,
                    Side: order.Side,
                    Quantity: order.Quantity,
                    OrderType: order.OrderType,
                    BarAtEmitClose: order.BarAtEmitClose is null ? null : (decimal)order.BarAtEmitClose.Value,
                    BarAtEmitTime: DateTime.TryParse(order.BarAtEmitTime, out var bt) ? bt : null,
                    Tag: order.Tag,
                    EmittedAtUtc: order.ReceivedAtUtc));
            }
            catch (Exception ex)
            {
                lf.CreateLogger("IngestEndpoints").LogError(ex,
                    "orders event-log write failed for {orderId} — pending queue still has the row",
                    order.OrderId);
            }

            return Results.Ok(new
            {
                accepted = true,
                orderId = order.OrderId,
                state = order.State.ToString().ToLowerInvariant(),
                receivedAtUtc = order.ReceivedAtUtc,
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
