using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// Read-side of the compare flow. Authenticated frontend users fetch the
/// most recent ranked-comparison payload that the Mac pushed in.
public static class CompareEndpoints
{
    public static IEndpointRouteBuilder MapCompareEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/compare").WithTags("Compare");

        group.MapGet("/universes", (ICompareStore store) =>
            Results.Ok(new { universes = store.ListUniverses() }));

        group.MapGet("/latest", (string universe, ICompareStore store) =>
        {
            if (string.IsNullOrWhiteSpace(universe))
                return Results.BadRequest(new { error = "universe is required" });

            var env = store.GetLatest(universe);
            if (env is null)
                return Results.NotFound(new { error = $"no compare payload pushed for universe '{universe}' yet" });

            // Return the original Python payload as-is, plus the receivedAt
            // timestamp the API stamped on ingest.
            return Results.Ok(new
            {
                universe = env.Universe,
                runId = env.RunId,
                generatedAtUtc = env.GeneratedAtUtc,
                receivedAtUtc = env.ReceivedAtUtc,
                rankMetric = env.RankMetric,
                rowCount = env.RowCount,
                payload = env.Payload,
            });
        });

        return app;
    }
}
