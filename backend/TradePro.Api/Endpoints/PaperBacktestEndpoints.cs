using TradePro.Api.Data.Stores;
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

        // Promotion-lifecycle overrides for the strategy catalog. Each
        // strategy ships with a code default `status` (the Python
        // ClassVar); operators override here without redeploying. UI
        // reads ListAll into a dict and merges client-side, so a single
        // catalog GET + one overrides GET serves the whole Strategies
        // page state.
        var status = app.MapGroup("/paper/strategy-status").WithTags("PaperBacktest");
        status.MapGet("/", (IPaperStrategyStatusStore store) =>
            Results.Ok(new { overrides = store.ListAll() }));

        status.MapPost("/{strategyId}", (
            string strategyId,
            StatusUpdate payload,
            IPaperStrategyStatusStore store,
            HttpContext ctx
        ) =>
        {
            if (string.IsNullOrWhiteSpace(payload.Status))
                return Results.BadRequest(new { error = "status is required" });
            // Mirror the SQL CHECK so the API returns a clean 400 rather
            // than a Postgres CHECK violation 500.
            var allowed = new[] { "evaluating", "backtest-ok", "scheduled", "live-eligible" };
            if (!allowed.Contains(payload.Status))
                return Results.BadRequest(new
                {
                    error = $"status must be one of {string.Join(", ", allowed)}",
                });
            var who = ctx.User?.Identity?.Name
                ?? ctx.Request.Headers["X-User"].FirstOrDefault()
                ?? "anonymous";
            var row = store.Upsert(strategyId, payload.Status, who);
            return Results.Ok(row);
        });

        status.MapDelete("/{strategyId}", (string strategyId, IPaperStrategyStatusStore store) =>
        {
            var removed = store.Clear(strategyId);
            return removed ? Results.NoContent() : Results.NotFound();
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

        // Bulk-reject every Pending row. Used to clear stale legacy
        // rows the operator can't approve (broken ticker mappings,
        // schema changes, etc). Optional tickerLike filter narrows by
        // SQL LIKE — e.g. "%_US_EQ" to target only the broken FX rows
        // generated before commit 015204a, leaving healthy equity
        // rows alone.
        pending.MapPost("/reject-all", (
            BulkRejectBody body, IPendingOrdersStore store) =>
        {
            var count = store.RejectAllPending(
                tickerLikePattern: string.IsNullOrWhiteSpace(body.TickerLike) ? null : body.TickerLike,
                reason: string.IsNullOrWhiteSpace(body.Reason) ? "bulk_reject" : body.Reason);
            return Results.Ok(new { rejected = count });
        });

        pending.MapPost("/{orderId}/approve",
            async (string orderId, IPendingOrdersStore store, OrdersRepository ordersRepo,
                   Trading212DemoClient t212demo, ILoggerFactory lf, CancellationToken ct) =>
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
            var log = lf.CreateLogger("paper.pending-orders");

            // Phase 6: record the human approval on the orders log.
            // This currently mirrors a "risk approves" signal — when the
            // real RiskService lands, the manual click will become one
            // input to the risk decision rather than the decision itself.
            try { ordersRepo.RecordRiskDecision(orderId, "approve", "human approval"); }
            catch (Exception ex) { log.LogError(ex, "orders log: risk-decision write failed for {orderId}", orderId); }

            // Sign convention: positive = BUY, negative = SELL.
            // PLACEMENT goes through Trading212DemoClient — by type
            // contract, this can only ever hit demo.trading212.com.
            // The live client cannot be accidentally injected here.
            decimal signedQty = order.Side == "BUY"
                ? Math.Abs(order.Quantity)
                : -Math.Abs(order.Quantity);
            var result = await t212demo.PlaceMarketOrderAsync(
                order.T212Ticker, signedQty, ct);
            if (result.Error is not null)
            {
                var failed = store.MarkFailed(orderId, result.Error, result.ResponseBody);
                try
                {
                    ordersRepo.InsertEvent("order_place_failed", orderId, new
                    {
                        order_id = orderId,
                        error = result.Error,
                        http_status = result.HttpStatus,
                    });
                }
                catch (Exception ex) { log.LogError(ex, "orders log: place-failed event for {orderId}", orderId); }
                return Results.Ok(failed);
            }
            var placed = store.MarkPlaced(
                orderId, result.OrderId, result.Status, result.ResponseBody);

            // T212 market orders fill effectively at the placement bar's
            // close, but we don't have access to the bar here. Record a
            // fill at the order's emit-close price as our best estimate
            // — the Mac engine will overwrite with the true fill price
            // when it pushes the next session snapshot. Future iterations
            // will subscribe to T212's order-stream for the real number.
            try
            {
                var fillPrice = order.BarAtEmitClose ?? 0.0;
                if (fillPrice > 0)
                {
                    ordersRepo.InsertFill(new NewFill(
                        OrderId: orderId,
                        FillQty: (decimal)Math.Abs(signedQty),
                        FillPrice: (decimal)fillPrice,
                        BrokerOrderId: result.OrderId?.ToString(),
                        FilledAtUtc: DateTime.UtcNow));
                }
            }
            catch (Exception ex) { log.LogError(ex, "orders log: fill insert failed for {orderId}", orderId); }

            return Results.Ok(placed);
        });

        pending.MapPost("/{orderId}/reject",
            (string orderId, string? reason, IPendingOrdersStore store,
             OrdersRepository ordersRepo, ILoggerFactory lf) =>
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
            try { ordersRepo.RecordRiskDecision(orderId, "reject", reason ?? "human rejection"); }
            catch (Exception ex)
            {
                lf.CreateLogger("paper.pending-orders").LogError(ex,
                    "orders log: risk-decision (reject) write failed for {orderId}", orderId);
            }
            var rejected = store.MarkRejected(orderId, reason);
            return Results.Ok(rejected);
        });

        return app;
    }
}

/// <summary>POST body for /api/paper/strategy-status/{strategyId}.</summary>
public sealed record StatusUpdate(string Status);

/// <summary>POST body for /api/paper/pending-orders/reject-all.
/// TickerLike is a SQL LIKE pattern; null/empty = reject all Pending.</summary>
public sealed record BulkRejectBody(string? TickerLike, string? Reason);

