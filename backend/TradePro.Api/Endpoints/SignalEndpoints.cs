using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

public static class SignalEndpoints
{
    public static IEndpointRouteBuilder MapSignalEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/api/signals").WithTags("Signals");

        group.MapPost("/evaluate", async (SignalRequest req, ISignalEngine engine, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(req.Symbol))
                return Results.BadRequest(new { error = "symbol is required" });
            if (string.IsNullOrWhiteSpace(req.Strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            try
            {
                var decision = await engine.EvaluateAsync(req, ct);
                return Results.Ok(decision);
            }
            catch (ArgumentException ex)
            {
                return Results.BadRequest(new { error = ex.Message });
            }
        });

        group.MapPost("/scan", async (ScanRequest req, ISignalScanner scanner, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(req.Strategy))
                return Results.BadRequest(new { error = "strategy is required" });
            try
            {
                var result = await scanner.ScanAsync(req, ct);
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
