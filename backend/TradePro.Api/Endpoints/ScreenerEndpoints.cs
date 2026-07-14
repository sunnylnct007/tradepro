using Amazon;
using Amazon.Lambda;
using Amazon.Lambda.Model;

namespace TradePro.Api.Endpoints;

public static class ScreenerEndpoints
{
    private const string FunctionName = "tradepro-screener";

    public static IEndpointRouteBuilder MapScreenerEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/screener").WithTags("Screener");

        // POST /api/screener/run — invoke the Lambda asynchronously (fire-and-forget).
        // The Lambda sends wheel + swing emails when done; this returns immediately.
        group.MapPost("/run", async (IConfiguration config, CancellationToken ct) =>
        {
            try
            {
                var region = config["AWS:Region"] ?? "eu-west-2";
                using var client = new AmazonLambdaClient(RegionEndpoint.GetBySystemName(region));

                var request = new InvokeRequest
                {
                    FunctionName = FunctionName,
                    InvocationType = InvocationType.Event, // async — don't wait for result
                    Payload = "{}",
                };

                var resp = await client.InvokeAsync(request, ct);

                // 202 Accepted means async invoke accepted
                if (resp.StatusCode == 202)
                    return Results.Ok(new { status = "triggered", message = "Screener running — check your email in ~2 minutes." });

                return Results.Problem($"Lambda returned unexpected status {resp.StatusCode}");
            }
            catch (Exception ex)
            {
                return Results.Problem($"Failed to trigger screener: {ex.Message}");
            }
        });

        return app;
    }
}
