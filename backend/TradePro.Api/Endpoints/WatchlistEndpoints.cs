using TradePro.Api.Watchlists;

namespace TradePro.Api.Endpoints;

public static class WatchlistEndpoints
{
    public static IEndpointRouteBuilder MapWatchlistEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/watchlists").WithTags("Watchlists");

        group.MapGet("/", (IWatchlistStore store) => Results.Ok(new { names = store.Keys }));

        group.MapGet("/{name}", (string name, IWatchlistStore store) =>
        {
            var list = store.Get(name);
            return list is null ? Results.NotFound() : Results.Ok(list);
        });

        return app;
    }
}
