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

        // Historical earnings announcements for the PriceHistoryChart overlay.
        // Returns reported earnings only (EPS actual present); excludes upcoming.
        // Defaults to 5-year lookback (1825 days) — matches the chart default.
        // Empty on any failure so the chart degrades to "no markers" cleanly.
        group.MapGet("/earnings", async (
            string symbol,
            int? lookbackDays,
            YahooFinanceProvider yahoo,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var days = lookbackDays is > 0 ? lookbackDays.Value : 1825;
            var markers = await yahoo.GetEarningsMarkersAsync(symbol, days, ct);

            // Serialise with snake_case field names to match the Python layer's
            // historical_earnings shape (date / eps_actual / eps_estimate / surprise_pct).
            var payload = markers.Select(m => new
            {
                date = m.Date,
                eps_actual = m.EpsActual,
                eps_estimate = m.EpsEstimate,
                surprise_pct = m.SurprisePct,
            });

            return Results.Ok(new
            {
                symbol,
                lookback_days = days,
                earnings = payload,
            });
        });

        // Corporate actions overlay (dividends + splits) for PriceHistoryChart.
        // Returns events oldest-first within lookbackDays (default 1825 d = 5y).
        // Dividends show as "D" chips, splits as "S" chips on the price chart.
        group.MapGet("/corporate-actions", async (
            string symbol,
            int? lookbackDays,
            YahooFinanceProvider yahoo,
            CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(symbol))
                return Results.BadRequest(new { error = "symbol is required" });

            var days = lookbackDays is > 0 ? lookbackDays.Value : 1825;
            var actions = await yahoo.GetCorporateActionsAsync(symbol, days, ct);

            var payload = actions.Select(a => new
            {
                date = a.Date,
                type = a.Type,
                amount = a.Amount,
                ratio = a.Ratio,
            });

            return Results.Ok(new
            {
                symbol,
                lookback_days = days,
                actions = payload,
            });
        });

        return app;
    }
}
