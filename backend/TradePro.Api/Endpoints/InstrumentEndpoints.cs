using TradePro.Api.Models;
using TradePro.Api.Providers;

namespace TradePro.Api.Endpoints;

public static class InstrumentEndpoints
{
    public static IEndpointRouteBuilder MapInstrumentEndpoints(this IEndpointRouteBuilder app)
    {
        // Symbol picker autocomplete. The Signals + Compare pages
        // hit this on every keystroke (debounced client-side) so the
        // user can't fat-finger an invalid ticker into evaluateSignal
        // and get a Yahoo 500. Yahoo's unauth search endpoint backs
        // it today; T212 instruments enrichment is a follow-up.
        app.MapGet("/instruments/search",
            async (
                string? q,
                int? limit,
                YahooSearchProvider yahoo,
                CancellationToken ct) =>
            {
                var query = (q ?? string.Empty).Trim();
                if (query.Length < 1)
                {
                    return Results.Ok(new InstrumentSearchResponse(
                        query, 0, Array.Empty<InstrumentMatch>()));
                }
                var items = await yahoo.SearchAsync(query, limit ?? 10, ct);
                return Results.Ok(new InstrumentSearchResponse(
                    query, items.Count, items));
            });

        return app;
    }
}
