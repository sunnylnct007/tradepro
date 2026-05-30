using System.Security.Claims;
using TradePro.Api.Alerts;

namespace TradePro.Api.Endpoints;

/// Read side of the operational alert feed. The cockpit polls
/// GET /api/alerts to render the alert banner and can dismiss an alert
/// via POST /api/alerts/{id}/resolve. Producers post on the ingest group
/// (POST /api/ingest/alert) — see IngestEndpoints.
public static class AlertsEndpoints
{
    public static IEndpointRouteBuilder MapAlertsEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/alerts").WithTags("Alerts");

        // Active alerts for the cockpit banner. `limit` caps the payload;
        // the banner only ever shows a handful at once.
        group.MapGet("/", (IAlertStore store, int? limit) =>
        {
            var rows = store.ListActive(Math.Clamp(limit ?? 50, 1, 200));
            return Results.Ok(new
            {
                count = rows.Count,
                critical = rows.Count(r => r.Severity == "critical"),
                alerts = rows,
            });
        });

        // Dismiss / acknowledge an alert. resolved_by is the signed-in
        // user where available, else "ui".
        group.MapPost("/{id:guid}/resolve", (Guid id, IAlertStore store, ClaimsPrincipal user) =>
        {
            var by = user.FindFirstValue("user_id")
                     ?? user.FindFirstValue(ClaimTypes.NameIdentifier)
                     ?? user.Identity?.Name
                     ?? "ui";
            return store.Resolve(id, by)
                ? Results.Ok(new { resolved = true, id })
                : Results.NotFound(new { error = "alert not found or already resolved", id });
        });

        return app;
    }
}
