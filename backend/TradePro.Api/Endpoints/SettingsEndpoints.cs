using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

public static class SettingsEndpoints
{
    public static IEndpointRouteBuilder MapSettingsEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/settings").WithTags("Settings");

        // GET — readable by anyone in the AllowedUsers policy (i.e. the
        // logged-in user). Also called by the Mac comparator to read
        // live thresholds at run start; that side uses the API publicly
        // so settings GET is intentionally not gated by IngestToken.
        group.MapGet("", (ISettingsStore store) => Results.Ok(store.Get()));

        group.MapPut("", (AppSettings incoming, ISettingsStore store) =>
        {
            // Validate ranges so a typo in the UI can't poison the
            // comparator. Numbers outside these envelopes don't make
            // sense for the rule and would silently break verdicts.
            if (incoming.Sentiment is null)
            {
                return Results.BadRequest(new { error = "sentiment block is required" });
            }
            var s = incoming.Sentiment;
            if (s.MeanSentimentThreshold < -1.0 || s.MeanSentimentThreshold > 1.0)
            {
                return Results.BadRequest(new
                {
                    error = "sentiment.meanSentimentThreshold must be in [-1, 1]",
                    got = s.MeanSentimentThreshold,
                });
            }
            if (s.MinMaterialNegativeCount < 0 || s.MinMaterialNegativeCount > 50)
            {
                return Results.BadRequest(new
                {
                    error = "sentiment.minMaterialNegativeCount must be in [0, 50]",
                    got = s.MinMaterialNegativeCount,
                });
            }
            if (s.LookbackDays < 1 || s.LookbackDays > 60)
            {
                return Results.BadRequest(new
                {
                    error = "sentiment.lookbackDays must be in [1, 60]",
                    got = s.LookbackDays,
                });
            }
            return Results.Ok(store.Update(incoming));
        });

        return app;
    }
}
