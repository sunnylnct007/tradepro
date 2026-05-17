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

        return app;
    }
}
