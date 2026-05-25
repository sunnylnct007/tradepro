using TradePro.Api.Oms;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/oms — Order Management System surface. Backs the OMS UI page
/// (Phase 2) + the daemon's intent push (Phase 1d). Every order the
/// platform ever places flows through these endpoints.
/// </summary>
public static class OmsEndpoints
{
    public static IEndpointRouteBuilder MapOmsEndpoints(this IEndpointRouteBuilder app)
    {
        var orders = app.MapGroup("/oms/orders").WithTags("OMS");

        // List orders. ?state=PENDING_APPROVAL,SUBMITTED filters; absent
        // = all states. Newest first.
        orders.MapGet("/", async (string? states, int? limit, IOmsService oms) =>
        {
            var stateList = string.IsNullOrWhiteSpace(states)
                ? null
                : states.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            return Results.Ok(new
            {
                orders = await oms.ListAsync(stateList, limit ?? 100),
            });
        });

        orders.MapGet("/{orderId:guid}", async (Guid orderId, IOmsService oms) =>
        {
            var o = await oms.GetAsync(orderId);
            return o is null ? Results.NotFound() : Results.Ok(o);
        });

        orders.MapGet("/{orderId:guid}/events", async (Guid orderId, IOmsService oms) =>
        {
            var events = await oms.ListEventsAsync(orderId);
            return Results.Ok(new { events });
        });

        // Enqueue an intent. The daemon calls this after the strategy
        // emits orders. ClientOrderId from the caller doubles as the
        // idempotency key — retries with the same id return the same row.
        orders.MapPost("/", async (OrderIntent intent, HttpContext ctx, IOmsService oms) =>
        {
            if (intent.Qty <= 0)
                return Results.BadRequest(new { error = "qty must be > 0" });
            var actor = ResolveActor(ctx);
            try
            {
                var row = await oms.EnqueueAsync(intent, actor);
                return Results.Ok(row);
            }
            catch (Npgsql.PostgresException ex)
            {
                // CHECK constraint failure → 400 with a readable message
                // so the caller can fix the payload rather than seeing
                // a server-side 500.
                return Results.BadRequest(new { error = ex.MessageText });
            }
        });

        orders.MapPost("/{orderId:guid}/approve",
            async (Guid orderId, HttpContext ctx, IOmsService oms) =>
                await TransitionResult(() => oms.ApproveAsync(orderId, ResolveActor(ctx))));

        orders.MapPost("/{orderId:guid}/reject",
            async (Guid orderId, ReasonBody body, HttpContext ctx, IOmsService oms) =>
                await TransitionResult(() => oms.RejectAsync(orderId, ResolveActor(ctx), body.Reason)));

        orders.MapPost("/{orderId:guid}/cancel",
            async (Guid orderId, ReasonBody body, HttpContext ctx, IOmsService oms) =>
                await TransitionResult(() => oms.CancelAsync(orderId, ResolveActor(ctx), body.Reason)));

        // Record a fill. Called by the daemon's audit push (Phase 1d)
        // and, post-Phase 2, by the broker callback handler when a
        // real fill arrives. Idempotent at the OmsService layer via
        // FOR UPDATE on the parent row + delta math.
        orders.MapPost("/{orderId:guid}/fill",
            async (Guid orderId, FillBody body, HttpContext ctx, IOmsService oms) =>
            {
                if (body.Qty <= 0)
                    return Results.BadRequest(new { error = "qty must be > 0" });
                try
                {
                    var row = await oms.RecordFillAsync(
                        orderId,
                        body.Qty,
                        body.Price,
                        body.Fee,
                        string.IsNullOrWhiteSpace(body.Currency) ? "USD" : body.Currency,
                        body.BrokerFillId,
                        ResolveActor(ctx));
                    return Results.Ok(row);
                }
                catch (InvalidOperationException ex)
                {
                    return Results.Conflict(new { error = ex.Message });
                }
            });

        // ── mode toggle ───────────────────────────────────────────
        var mode = app.MapGroup("/oms/mode").WithTags("OMS");

        mode.MapGet("/", (IOmsModeService svc) =>
            Results.Ok(new { mode = svc.Current.ToString().ToLowerInvariant() }));

        mode.MapPost("/", async (ModeBody body, HttpContext ctx, IOmsModeService svc) =>
        {
            if (!Enum.TryParse<OmsMode>(body.Mode, ignoreCase: true, out var target))
                return Results.BadRequest(new { error = "mode must be 'auto' or 'manual'" });
            var prior = svc.Current;
            var now = await svc.SetAsync(target, ResolveActor(ctx));
            return Results.Ok(new
            {
                mode = now.ToString().ToLowerInvariant(),
                prior = prior.ToString().ToLowerInvariant(),
            });
        });

        return app;
    }

    private static async Task<IResult> TransitionResult(Func<Task<OmsOrder>> action)
    {
        try
        {
            var row = await action();
            return Results.Ok(row);
        }
        catch (InvalidOperationException ex)
        {
            // State-machine guard tripped (wrong prior state) — return
            // 409 Conflict so the UI can re-fetch and re-render rather
            // than treating it as a generic 500.
            return Results.Conflict(new { error = ex.Message });
        }
    }

    private static string ResolveActor(HttpContext ctx) =>
        ctx.User?.Identity?.Name
        ?? ctx.Request.Headers["X-User"].FirstOrDefault()
        ?? "anonymous";

    public sealed record ReasonBody(string Reason);
    public sealed record ModeBody(string Mode);
    public sealed record FillBody(
        decimal Qty,
        decimal Price,
        decimal Fee = 0,
        string? Currency = null,
        string? BrokerFillId = null
    );
}
