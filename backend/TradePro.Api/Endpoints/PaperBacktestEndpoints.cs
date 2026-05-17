using TradePro.Api.Providers.Trading212;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// <summary>
/// Reads paper-trading backtest reports the Mac pushed via the
/// `/ingest/paper-backtest` route. The frontend Backtest page hits
/// these to list + drill into per-strategy comparator results.
/// </summary>
public static class PaperBacktestEndpoints
{
    public static IEndpointRouteBuilder MapPaperBacktestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/paper/backtest").WithTags("PaperBacktest");

        // List the most recent reports, newest first. Capped at 50 by
        // default — the UI lazy-loads more if it needs them.
        group.MapGet("/reports", (IPaperBacktestStore store, int? limit) =>
            Results.Ok(store.List(limit ?? 50)));

        // Full payload for one report. 404 if the in-memory store has
        // forgotten it (post-restart). UI shows a "report no longer
        // available; re-run the backtest from the Mac" message.
        group.MapGet("/reports/{reportId}", (string reportId, IPaperBacktestStore store) =>
        {
            var env = store.Get(reportId);
            return env is null ? Results.NotFound() : Results.Ok(env.Payload);
        });

        // Catalog of registered paper-trading strategies pushed from
        // the Mac (`tradepro-paper-strategies-push`). 404 until the
        // Mac has pushed once — the UI handles that gracefully with
        // a "run tradepro-paper-strategies-push to populate" hint.
        var catalog = app.MapGroup("/paper/strategies").WithTags("PaperBacktest");
        catalog.MapGet("/", (IPaperStrategiesStore store) =>
        {
            var cur = store.Get();
            return cur is null ? Results.NotFound() : Results.Ok(cur);
        });

        // Live snapshots from the latest paper-engine sessions — full
        // ledger (positions + recent fills + P&L) per session label.
        // Powers the Live tab on the Paper page.
        var snaps = app.MapGroup("/paper/snapshots").WithTags("PaperBacktest");
        snaps.MapGet("/", (IPaperSnapshotStore store, int? limit) =>
            Results.Ok(store.List(limit ?? 50)));
        snaps.MapGet("/{sessionLabel}", (string sessionLabel, IPaperSnapshotStore store) =>
        {
            var env = store.Get(sessionLabel);
            return env is null ? Results.NotFound() : Results.Ok(env.Payload);
        });

        // Pending paper orders (manual-mode placement). UI reads here
        // to render the "Pending orders" panel; Approve / Reject
        // buttons hit the POST endpoints below. The Approve endpoint
        // is what actually places the order against T212 (using the
        // backend's own Trading212Client) — the Mac engine never
        // touches T212 in manual mode.
        var pending = app.MapGroup("/paper/pending-orders").WithTags("PaperBacktest");
        pending.MapGet("/", (IPendingOrdersStore store) => Results.Ok(store.List()));

        pending.MapPost("/{orderId}/approve",
            async (string orderId, IPendingOrdersStore store, Trading212Client t212, CancellationToken ct) =>
        {
            var order = store.Get(orderId);
            if (order is null) return Results.NotFound();
            if (order.State != PendingOrderState.Pending)
            {
                return Results.BadRequest(new
                {
                    error = $"order is {order.State.ToString().ToLowerInvariant()}, cannot approve",
                });
            }
            // Sign convention: positive = BUY, negative = SELL.
            decimal signedQty = order.Side == "BUY"
                ? Math.Abs(order.Quantity)
                : -Math.Abs(order.Quantity);
            var result = await t212.PlaceMarketOrderAsync(
                order.T212Ticker, signedQty, ct);
            if (result.Error is not null)
            {
                var failed = store.MarkFailed(orderId, result.Error, result.ResponseBody);
                return Results.Ok(failed);
            }
            var placed = store.MarkPlaced(
                orderId, result.OrderId, result.Status, result.ResponseBody);
            return Results.Ok(placed);
        });

        pending.MapPost("/{orderId}/reject",
            (string orderId, string? reason, IPendingOrdersStore store) =>
        {
            var order = store.Get(orderId);
            if (order is null) return Results.NotFound();
            if (order.State != PendingOrderState.Pending)
            {
                return Results.BadRequest(new
                {
                    error = $"order is {order.State.ToString().ToLowerInvariant()}, cannot reject",
                });
            }
            var rejected = store.MarkRejected(orderId, reason);
            return Results.Ok(rejected);
        });

        return app;
    }
}
