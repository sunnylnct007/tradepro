using System.Text.Json;
using Amazon.SQS;
using Amazon.SQS.Model;

namespace TradePro.Api.Providers;

/// <summary>
/// Sends a fire-and-forget message to the SQS paper-trigger queue when a
/// paper session is enqueued via POST /api/ops/run-paper.
///
/// Configuration (env vars):
///   TRADEPRO_SQS_TRIGGER_URL  — queue URL from terraform output
///                                `tradepro_sqs_trigger_url`.
///                                If absent the service is a no-op
///                                (REST polling is the Mac's fallback).
///   AWS_REGION                — defaults to eu-west-2 (matches the queue).
///
/// The EC2 instance IAM role grants sqs:SendMessage on this queue ARN.
/// The Mac developer uses the infoccit-admin SSO profile which has
/// account-wide SQS access via the queue resource policy.
/// </summary>
public sealed class SqsTriggerService
{
    private readonly string? _queueUrl;
    private readonly ILogger<SqsTriggerService> _log;
    private readonly IAmazonSQS? _sqs;

    public SqsTriggerService(ILogger<SqsTriggerService> log, IConfiguration cfg)
    {
        _log = log;
        _queueUrl = cfg["TRADEPRO_SQS_TRIGGER_URL"]
                 ?? Environment.GetEnvironmentVariable("TRADEPRO_SQS_TRIGGER_URL");

        if (string.IsNullOrWhiteSpace(_queueUrl))
        {
            _log.LogDebug("SqsTriggerService: TRADEPRO_SQS_TRIGGER_URL not set — SQS disabled, Mac will fall back to REST polling");
            return;
        }

        var region = cfg["AWS_REGION"]
                  ?? Environment.GetEnvironmentVariable("AWS_REGION")
                  ?? "eu-west-2";

        try
        {
            _sqs = new AmazonSQSClient(Amazon.RegionEndpoint.GetBySystemName(region));
            _log.LogInformation("SqsTriggerService ready: queue={QueueUrl} region={Region}", _queueUrl, region);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "SqsTriggerService: failed to create SQS client — SQS disabled");
        }
    }

    /// <summary>
    /// Send a trigger message. Fire-and-forget: never blocks the caller,
    /// never throws. SQS delivery is best-effort here; the DB row is the
    /// authoritative record.
    /// </summary>
    public void SendTrigger(string requestId, JsonElement paramsEl)
    {
        if (_sqs is null || string.IsNullOrWhiteSpace(_queueUrl)) return;

        // Don't await — fire and forget on the thread-pool.
        _ = Task.Run(async () =>
        {
            try
            {
                var body = JsonSerializer.Serialize(new
                {
                    request_id = requestId,
                    @params    = paramsEl,
                });
                await _sqs.SendMessageAsync(new SendMessageRequest
                {
                    QueueUrl    = _queueUrl,
                    MessageBody = body,
                });
                _log.LogInformation("SqsTriggerService: sent trigger for request {RequestId}", requestId);
            }
            catch (Exception ex)
            {
                // Fail-open: log but never surface to caller. Mac REST
                // polling is the safety net.
                _log.LogWarning(ex, "SqsTriggerService: failed to send SQS message for {RequestId} — Mac will pick it up via REST polling", requestId);
            }
        });
    }
}
