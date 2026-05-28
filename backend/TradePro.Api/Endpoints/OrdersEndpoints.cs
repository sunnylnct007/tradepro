using System.Text;
using System.Text.Json;
using TradePro.Api.Data;
using TradePro.Api.Data.Stores;

namespace TradePro.Api.Endpoints;

/// <summary>
/// Read API over the event-sourced orders + fills + events + positions
/// view. Phase 6 of the unicorn architecture. The Decide/Paper pages
/// hit these endpoints when they want auditability — "show me every
/// order this strategy emitted on AVGO, with the fills and the risk
/// decision trail."
///
/// Reads only — writes happen as side-effects of /api/ingest/* and
/// /api/paper/pending-orders/{id}/approve|reject. Don't expose a write
/// path here; that would let a UI editor mutate the event log, which
/// defeats the purpose.
/// </summary>
public static class OrdersEndpoints
{
    public static IEndpointRouteBuilder MapOrdersEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/orders").WithTags("Orders");

        // List the most-recent orders. Optional symbol filter; default
        // limit 100 — matches the UI's expected page size.
        group.MapGet("/", (OrdersRepository repo, string? symbol, int? limit) =>
            Results.Ok(repo.List(limit ?? 100, symbol)));

        // Single order + its fills, joined. UI displays this as a
        // detail panel: order header on top, fills as rows, decision
        // trace expandable.
        group.MapGet("/{orderId}", (string orderId, OrdersRepository repo) =>
        {
            var order = repo.ReadOne(orderId);
            if (order is null) return Results.NotFound();
            var fills = repo.ListFills(orderId);
            return Results.Ok(new { order, fills });
        });

        // Just the fills for an order — used when the UI already has
        // the order object cached and is polling for new fills.
        group.MapGet("/{orderId}/fills", (string orderId, OrdersRepository repo) =>
            Results.Ok(repo.ListFills(orderId)));

        // Domain event log. Optional type filter so e.g. the SSE-stream
        // bootstrap can hydrate "last N order_emitted events" before
        // subscribing to live.
        var events = app.MapGroup("/events").WithTags("Events");
        events.MapGet("/", (OrdersRepository repo, string? type, int? limit) =>
            Results.Ok(repo.ListEvents(limit ?? 100, type)));

        // SSE stream — Phase 7 of VISION.md. Subscribers see every
        // domain event the moment it lands in Postgres. ?since=<seq>
        // catches up missed events on reconnect; ?type= filters to one
        // event_type. The handler holds a long-lived LISTEN connection
        // — EventStream owns the lifetime.
        //
        // Note: SSE in browsers doesn't allow custom headers via
        // EventSource. The frontend uses streaming fetch instead so it
        // can attach the bearer token. Same SSE wire format either way.
        events.MapGet("/stream", async (
            HttpContext ctx, EventStream stream, long? since, string? type) =>
        {
            ctx.Response.Headers["Content-Type"] = "text/event-stream";
            ctx.Response.Headers["Cache-Control"] = "no-cache, no-transform";
            ctx.Response.Headers["X-Accel-Buffering"] = "no"; // disable nginx buffering
            ctx.Response.Headers["Connection"] = "keep-alive";

            // Emit a tiny preamble so the client knows the stream
            // opened. Some proxies don't flush headers until the
            // first body byte.
            await ctx.Response.WriteAsync(": ok\n\n", ctx.RequestAborted);
            await ctx.Response.Body.FlushAsync(ctx.RequestAborted);

            await foreach (var ev in stream.StreamAsync(since, type, ctx.RequestAborted))
            {
                if (ev is null)
                {
                    // Keepalive — comment frame; clients ignore but
                    // proxies see traffic.
                    await ctx.Response.WriteAsync(": keepalive\n\n", ctx.RequestAborted);
                }
                else
                {
                    var json = JsonSerializer.Serialize(ev);
                    var frame = new StringBuilder()
                        .Append("id: ").Append(ev.Seq).Append('\n')
                        .Append("event: ").Append(ev.EventType).Append('\n')
                        .Append("data: ").Append(json).Append("\n\n");
                    await ctx.Response.WriteAsync(frame.ToString(), ctx.RequestAborted);
                }
                await ctx.Response.Body.FlushAsync(ctx.RequestAborted);
            }
        });

        return app;
    }
}
