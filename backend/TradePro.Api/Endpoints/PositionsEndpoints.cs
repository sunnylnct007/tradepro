using Dapper;
using Npgsql;
using TradePro.Api.Positions;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/positions/* — position-state surface backed by the
/// broker-as-golden model (see PositionReconciler.cs).
///
/// Today-only by default per the no-clutter principle:
///   /drift             — open (unresolved) drift only
///   /drift?since=...   — historical lookup with explicit filter
/// </summary>
public static class PositionsEndpoints
{
    public static IEndpointRouteBuilder MapPositionsEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/positions").WithTags("Positions");

        // POST /api/positions/reconcile?broker=t212_demo
        // Trigger a reconciliation pass for one broker. Returns the new
        // drift events created (already persisted to position_drift_events).
        // Operator-initiated; also called from the scheduled CLI.
        group.MapPost("/reconcile", async (
            string? broker, PositionReconciler reconciler, CancellationToken ct) =>
        {
            var b = (broker ?? "t212_demo").ToLowerInvariant();
            PositionReconciler.ReconcileResult result = b switch
            {
                "t212_demo" => await reconciler.ReconcileT212DemoAsync(ct),
                _ => throw new ArgumentException(
                    $"unknown broker {broker} — only t212_demo wired today"),
            };
            return Results.Ok(new
            {
                broker = result.Broker,
                brokerPositions = result.BrokerPositions,
                internalPositions = result.InternalPositions,
                eventsCreated = result.EventsCreated.Count,
                error = result.Error,
                drift = result.EventsCreated.Select(e => new
                {
                    broker = e.Broker, symbol = e.Symbol,
                    brokerQty = e.BrokerQty, internalQty = e.InternalQty,
                    qtyDrift = e.QtyDrift,
                    brokerAvgPrice = e.BrokerAvgPrice,
                    internalAvgPrice = e.InternalAvgPrice,
                    priceDriftPct = e.PriceDriftPct,
                    severity = e.Severity,
                }),
            });
        });

        // GET /api/positions/drift?unresolved=true&severity=&since=&limit=
        // Default behaviour: unresolved-only, today-only. Banner reads
        // this with no params to get "currently open drift" without
        // historical clutter.
        group.MapGet("/drift", async (
            bool? unresolved, string? severity, DateTime? since, int? limit,
            NpgsqlDataSource db) =>
        {
            var unresolvedOnly = unresolved ?? true;
            var lim = Math.Clamp(limit ?? 50, 1, 200);
            var clauses = new List<string>();
            var parms = new Dictionary<string, object>();
            if (unresolvedOnly) clauses.Add("resolved_at_utc IS NULL");
            if (!string.IsNullOrWhiteSpace(severity))
            {
                clauses.Add("severity = @severity");
                parms["severity"] = severity!.ToLowerInvariant();
            }
            if (since.HasValue)
            {
                clauses.Add("detected_at_utc >= @since");
                parms["since"] = since.Value;
            }
            var where = clauses.Count > 0 ? "WHERE " + string.Join(" AND ", clauses) : "";
            parms["lim"] = lim;
            await using var conn = await db.OpenConnectionAsync();
            var rows = await conn.QueryAsync<DriftRow>($@"
                SELECT id, broker, symbol,
                       broker_qty AS BrokerQty,
                       internal_qty AS InternalQty,
                       qty_drift AS QtyDrift,
                       broker_avg_price AS BrokerAvgPrice,
                       internal_avg_price AS InternalAvgPrice,
                       price_drift_pct AS PriceDriftPct,
                       severity,
                       detected_at_utc AS DetectedAtUtc,
                       resolved_at_utc AS ResolvedAtUtc,
                       resolved_by AS ResolvedBy,
                       resolution_note AS ResolutionNote
                FROM position_drift_events
                {where}
                ORDER BY detected_at_utc DESC
                LIMIT @lim;", parms);
            return Results.Ok(new
            {
                drift = rows.Select(r => new
                {
                    id = r.Id, broker = r.Broker, symbol = r.Symbol,
                    brokerQty = r.BrokerQty, internalQty = r.InternalQty,
                    qtyDrift = r.QtyDrift,
                    brokerAvgPrice = r.BrokerAvgPrice,
                    internalAvgPrice = r.InternalAvgPrice,
                    priceDriftPct = r.PriceDriftPct,
                    severity = r.Severity,
                    detectedAtUtc = r.DetectedAtUtc,
                    resolvedAtUtc = r.ResolvedAtUtc,
                    resolvedBy = r.ResolvedBy,
                    resolutionNote = r.ResolutionNote,
                }),
            });
        });

        // POST /api/positions/drift/{id}/resolve
        // Mark a drift event resolved. Body: { resolvedBy?, note? }.
        // No auto-resolution — every drift requires a human acknowledgement.
        group.MapPost("/drift/{id:long}/resolve", async (
            long id, ResolveDriftBody? body,
            HttpContext ctx, PositionReconciler reconciler, CancellationToken ct) =>
        {
            var resolvedBy = body?.ResolvedBy
                ?? ctx.User?.Identity?.Name
                ?? "ui";
            var ok = await reconciler.ResolveAsync(id, resolvedBy, body?.Note, ct);
            return ok
                ? Results.Ok(new { resolved = true, id })
                : Results.NotFound(new { error = $"no open drift with id {id}" });
        });

        return app;
    }

    private sealed record DriftRow(
        long Id, string Broker, string Symbol,
        decimal? BrokerQty, decimal? InternalQty, decimal QtyDrift,
        decimal? BrokerAvgPrice, decimal? InternalAvgPrice, decimal? PriceDriftPct,
        string Severity, DateTime DetectedAtUtc,
        DateTime? ResolvedAtUtc, string? ResolvedBy, string? ResolutionNote);

    public sealed record ResolveDriftBody(string? ResolvedBy, string? Note);
}
