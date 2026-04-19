using TradePro.Api.Providers;

namespace TradePro.Api.Endpoints;

public static class MarketDataEndpoints
{
    public static IEndpointRouteBuilder MapMarketDataEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/marketdata").WithTags("MarketData");

        group.MapGet("/providers", (IMarketDataRegistry registry) =>
            Results.Ok(new { providers = registry.AvailableProviders }));

        group.MapGet("/candles", async (
            string symbol,
            string? provider,
            string? interval,
            DateTime? from,
            DateTime? to,
            IMarketDataRegistry registry,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var p = registry.Resolve(provider);
            var fromDate = from ?? DateTime.UtcNow.AddYears(-1);
            var toDate = to ?? DateTime.UtcNow;
            var series = await p.GetCandlesAsync(symbol, interval ?? "1d", fromDate, toDate, ct);
            return Results.Ok(series);
        });

        return app;
    }
}
