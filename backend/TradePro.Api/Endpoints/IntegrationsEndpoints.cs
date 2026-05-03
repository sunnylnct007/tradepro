using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Endpoints;

public static class IntegrationsEndpoints
{
    public static IEndpointRouteBuilder MapIntegrationsEndpoints(this IEndpointRouteBuilder app)
    {
        // Surfaces whether the T212 layer can reach the broker with the
        // current config — used by the Settings page to confirm a key
        // pair is live before we let the user save it.
        app.MapGet("/integrations/trading212/status",
            async (Trading212Client client, CancellationToken ct) =>
                Results.Ok(await client.GetStatusAsync(ct)));

        return app;
    }
}
