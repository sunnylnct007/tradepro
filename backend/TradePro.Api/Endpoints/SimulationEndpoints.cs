using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

public static class SimulationEndpoints
{
    public static IEndpointRouteBuilder MapSimulationEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/api/simulations").WithTags("Simulations");

        group.MapGet("/strategies", (IStrategyRegistry reg) =>
            Results.Ok(new { strategies = reg.AvailableStrategies }));

        group.MapPost("/run", async (SimulationRequest req, ISimulator sim, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(req.Symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            if (req.InitialCapital <= 0m)
                return Results.BadRequest(new { error = "initialCapital must be > 0" });
            if (req.From >= req.To)
                return Results.BadRequest(new { error = "from must be before to" });

            try
            {
                var result = await sim.RunAsync(req, ct);
                return Results.Ok(result);
            }
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
        });

        return app;
    }
}
