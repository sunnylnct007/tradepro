namespace TradePro.Api.Endpoints;

public static class WatchlistEndpoints
{
    public static IEndpointRouteBuilder MapWatchlistEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/api/watchlists").WithTags("Watchlists");

        // Hand-seeded UK preset so you can demo the stack without typing symbols.
        // Move to config/persistent storage in Phase 1.
        group.MapGet("/uk", () => Results.Ok(new
        {
            name = "UK — Large Caps & Index",
            currency = "GBP",
            region = "UK",
            items = new[]
            {
                new { symbol = "^FTSE",  label = "FTSE 100 Index",      kind = "index" },
                new { symbol = "^FTMC",  label = "FTSE 250 Index",      kind = "index" },
                new { symbol = "BARC.L", label = "Barclays",            kind = "equity" },
                new { symbol = "LLOY.L", label = "Lloyds Banking Group",kind = "equity" },
                new { symbol = "HSBA.L", label = "HSBC Holdings",       kind = "equity" },
                new { symbol = "SHEL.L", label = "Shell",               kind = "equity" },
                new { symbol = "AZN.L",  label = "AstraZeneca",         kind = "equity" },
                new { symbol = "ULVR.L", label = "Unilever",            kind = "equity" },
                new { symbol = "GSK.L",  label = "GSK",                 kind = "equity" },
                new { symbol = "BP.L",   label = "BP",                  kind = "equity" }
            }
        }));

        return app;
    }
}
