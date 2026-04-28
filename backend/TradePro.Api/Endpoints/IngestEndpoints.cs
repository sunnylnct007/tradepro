using System.Text.Json;
using TradePro.Api.Auth;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// Receives result payloads pushed from the local Mac (`tradepro-push`).
/// Auth: `Authorization: Bearer <Ingest:Token>` — separate from the
/// Firebase login the frontend uses, because there's no human at the Mac.
public static class IngestEndpoints
{
    public static IEndpointRouteBuilder MapIngestEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/ingest")
            .WithTags("Ingest")
            .RequireAuthorization(IngestTokenAuth.Policy);

        // The Python tradepro-push expects the POST to succeed with any 2xx
        // and prints HTTP/text on failure. Return a small JSON ack so logs
        // are useful on both ends.
        group.MapPost("/compare", (JsonElement payload, ICompareStore store) =>
        {
            if (payload.ValueKind != JsonValueKind.Object)
            {
                return Results.BadRequest(new { error = "payload must be a JSON object" });
            }
            var env = store.Put(payload);
            return Results.Ok(new
            {
                accepted = true,
                universe = env.Universe,
                runId = env.RunId,
                rowCount = env.RowCount,
                generatedAtUtc = env.GeneratedAtUtc,
                receivedAtUtc = env.ReceivedAtUtc,
            });
        });

        return app;
    }
}
