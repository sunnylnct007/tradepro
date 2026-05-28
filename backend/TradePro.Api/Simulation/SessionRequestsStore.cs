using System.Text.Json;

namespace TradePro.Api.Simulation;

/// <summary>
/// Trigger queue for ops the user wants the Mac engine to run.
/// Backs Task #69 (intraday automation): the UI POSTs intent →
/// row goes 'Pending' → Mac polls and flips it to 'Claimed' → Mac
/// runs the op → posts back 'Completed' or 'Failed'.
///
/// Claim is atomic (single UPDATE ... RETURNING) so two workers
/// can't pick the same row even if they poll at the same time.
/// </summary>
public interface ISessionRequestsStore
{
    SessionRequest Put(string kind, JsonElement? params_);
    SessionRequest? Get(string requestId);
    SessionRequest? Claim(string kind, string claimedBy);
    SessionRequest? MarkCompleted(string requestId, JsonElement? resultSummary);
    SessionRequest? MarkFailed(string requestId, string error);
    SessionRequest? Cancel(string requestId);
    IReadOnlyList<SessionRequest> List(string? kind, int limit = 100);
}

public enum SessionRequestState
{
    Pending,
    Claimed,
    Completed,
    Failed,
    Cancelled,
}

public sealed record SessionRequest(
    string RequestId,
    string Kind,
    JsonElement Params,
    SessionRequestState State,
    DateTime RequestedAtUtc,
    DateTime? ClaimedAtUtc,
    string? ClaimedBy,
    DateTime? CompletedAtUtc,
    JsonElement? ResultSummary,
    string? Error);
