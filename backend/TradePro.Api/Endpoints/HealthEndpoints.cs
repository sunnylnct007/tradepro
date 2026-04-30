using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

public static class HealthEndpoints
{
    public static IEndpointRouteBuilder MapHealthEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/health", () => Results.Ok(new
        {
            status = "ok",
            service = "tradepro-api",
            utc = DateTime.UtcNow
        }));

        // Friendly index at the root — without it, opening
        // http://localhost:5080/ returns a bare 404 and a confused
        // user thinks the API is down. Lists the things they probably
        // wanted instead and the count of compare payloads currently
        // cached on disk so they can spot a freshness issue at a glance.
        app.MapGet("/", (ICompareStore store, IHostEnvironment env) =>
        {
            var summaries = store.ListUniverses();
            return Results.Ok(new
            {
                service = "tradepro-api",
                utc = DateTime.UtcNow,
                environment = env.EnvironmentName,
                links = new
                {
                    health = "/health",
                    swagger = env.IsDevelopment() ? "/swagger" : null,
                    compare_universes = "/api/compare/universes",
                    compare_latest = "/api/compare/latest?universe=etf_us_core",
                },
                compare_cache = new
                {
                    universes = summaries.Count,
                    items = summaries,
                },
            });
        });

        return app;
    }
}
