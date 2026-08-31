using Amazon;
using Amazon.Lambda;
using Amazon.Lambda.Model;

namespace TradePro.Api.Endpoints;

/// <summary>
/// /api/jobs — run a scheduled job ON DEMAND, from the UI, without the Mac.
///
/// Owner, 29 Aug 2026: *"just remember i need UI function t trigger them as I
/// need it"*, alongside moving the scheduled jobs off the laptop — *"macbook
/// was choosen for runnign llm ... all these light scedhued jobs can mive to
/// lambda"*.
///
/// WHY NOT THE EXISTING BUTTON. The desk already has a "Run now" control, but
/// it writes a Firestore document that a MAC-SIDE worker polls. That is the
/// exact dependency the Lambda migration exists to remove: it needs the laptop
/// awake, and it silently does nothing when the laptop is shut or travelling.
/// This path invokes the Lambda directly, so a run works from a phone with the
/// Mac closed.
///
/// SYNCHRONOUS ON PURPOSE. These jobs finish in seconds (the strangle paper run
/// takes ~8s cold), and the useful answer is the job's OWN result — how many
/// candidates, or the reason there were none. Fire-and-forget would return
/// "queued" and leave the operator to guess, which is what the Firestore path
/// already does badly.
///
/// The job name is validated against a fixed allow-list. This endpoint can
/// invoke one specific Lambda with one specific shape of payload and nothing
/// else — it is not a general-purpose AWS console.
/// </summary>
public static class JobsEndpoints
{
    private const string FunctionName = "tradepro-jobs";
    private const string DefaultRegion = "eu-west-2";

    /// <summary>The jobs the UI may trigger. MUST match the JOBS registry in
    /// strategies/lambda_handler.py — an unknown name there returns a readable
    /// error listing what it does know, so a drift shows up as a message rather
    /// than a silent no-op. Kept short deliberately: this is a trigger for the
    /// daily screens, not a remote shell.</summary>
    private static readonly HashSet<string> Allowed = new(StringComparer.OrdinalIgnoreCase)
    {
        "index_strangle_paper",
        "index_strangle_alert",
        "post_earnings_puts",
        "index_strangle_close",
    };

    public static IEndpointRouteBuilder MapJobsEndpoints(this IEndpointRouteBuilder app)
    {
        var g = app.MapGroup("/jobs").WithTags("Jobs");

        // GET /api/jobs — what can be triggered.
        g.MapGet("/", () => Results.Ok(new
        {
            function = FunctionName,
            jobs = Allowed.OrderBy(x => x).ToArray(),
            note = "POST /api/jobs/{job}/run to trigger. Runs in Lambda, not on the Mac.",
        }));

        // POST /api/jobs/{job}/run
        g.MapPost("/{job}/run", async (string job, ILoggerFactory lf, CancellationToken ct) =>
        {
            var log = lf.CreateLogger("JobTrigger");
            if (string.IsNullOrWhiteSpace(job) || !Allowed.Contains(job))
                return Results.BadRequest(new
                {
                    error = $"unknown job '{job}'",
                    known = Allowed.OrderBy(x => x).ToArray(),
                });

            var region = System.Environment.GetEnvironmentVariable("AWS_REGION") ?? DefaultRegion;
            log.LogInformation("UI triggered job {Job} on {Function} ({Region})",
                job, FunctionName, region);
            try
            {
                using var client = new AmazonLambdaClient(RegionEndpoint.GetBySystemName(region));
                var res = await client.InvokeAsync(new InvokeRequest
                {
                    FunctionName = FunctionName,
                    InvocationType = InvocationType.RequestResponse,
                    Payload = System.Text.Json.JsonSerializer.Serialize(new { job }),
                }, ct);

                using var reader = new StreamReader(res.Payload);
                var body = await reader.ReadToEndAsync(ct);

                // FAIL LOUD, and distinguish the two failures that look alike:
                // the invoke not happening at all (permissions, region, wrong
                // function) versus the job running and failing. Reporting both
                // as "failed" is how an IAM problem gets mistaken for a broken
                // screen.
                if (res.FunctionError is not null)
                {
                    log.LogError("Job {Job} raised inside Lambda: {Err} {Body}",
                        job, res.FunctionError, body);
                    return Results.Json(new
                    {
                        ok = false, job, stage = "job",
                        error = res.FunctionError, body,
                    }, statusCode: 502);
                }
                log.LogInformation("Job {Job} returned {Status}", job, res.StatusCode);
                return Results.Json(new
                {
                    ok = true, job, statusCode = res.StatusCode,
                    result = body,
                });
            }
            catch (AmazonLambdaException ex)
            {
                // Permissions/region/function-not-found land here, NOT above.
                log.LogError(ex, "Could not invoke {Function} for job {Job}", FunctionName, job);
                return Results.Json(new
                {
                    ok = false, job, stage = "invoke",
                    error = ex.Message,
                    hint = "the API's IAM role needs lambda:InvokeFunction on "
                         + $"{FunctionName}; check the role, not the job",
                }, statusCode: 502);
            }
        })
        .WithName("RunJobNow");

        return app;
    }
}
