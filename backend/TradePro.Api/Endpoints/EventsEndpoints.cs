using TradePro.Api.Data;

namespace TradePro.Api.Endpoints;

/// <summary>
/// GET /api/events/oms — Server-Sent Events stream for OMS-relevant
/// domain events (new orders, fills, risk decisions).
///
/// EventSource in the browser cannot send custom headers, so this
/// endpoint is registered on the root app (not the /api auth group)
/// and marked AllowAnonymous. The data emitted is minimal — seq +
/// event type + aggregateId — no sensitive payload.
/// </summary>
public static class EventsEndpoints
{
    private static readonly HashSet<string> OmsEventTypes = new(StringComparer.Ordinal)
    {
        "order_emitted",
        "fill_received",
        "order_risk_approved",
        "order_risk_rejected",
    };

    public static IEndpointRouteBuilder MapEventsEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/api/events/oms", async (
            long? since,
            EventStream stream,
            HttpContext ctx,
            CancellationToken ct) =>
        {
            ctx.Response.Headers["Content-Type"]      = "text/event-stream";
            ctx.Response.Headers["Cache-Control"]     = "no-cache";
            ctx.Response.Headers["X-Accel-Buffering"] = "no";
            ctx.Response.Headers["Connection"]        = "keep-alive";

            await foreach (var ev in stream.StreamAsync(since, null, ct))
            {
                if (ev is null)
                {
                    // Keepalive comment — prevents reverse-proxy timeouts.
                    await ctx.Response.WriteAsync(": ping\n\n", ct);
                }
                else
                {
                    if (!OmsEventTypes.Contains(ev.EventType)) continue;

                    var aggId = ev.AggregateId is null ? "null" : $"\"{ev.AggregateId}\"";
                    var line = $"data: {{\"seq\":{ev.Seq},\"type\":\"{ev.EventType}\",\"aggregateId\":{aggId},\"occurredAt\":\"{ev.OccurredAt:O}\"}}\n\n";
                    await ctx.Response.WriteAsync(line, ct);
                }

                await ctx.Response.Body.FlushAsync(ct);
            }
        })
        .AllowAnonymous()
        .WithTags("Events");

        return app;
    }
}
