using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// Classifier coverage for the /iserver/marketdata/history bounded retry:
/// the two transient signatures observed live 2026-08-09 (a 0/20 failure
/// burst where an immediate re-request succeeded) must be retried; contract
/// and validation errors must NOT be (retrying those wastes the pacing
/// budget and delays the yfinance fallback).
/// </summary>
public class IBKRHistoryTransientTest
{
    [Theory]
    // Cold chart cache — IBKR fails fast with this body; the failed request
    // itself warms the cache, so a retry succeeds.
    [InlineData(400, "{\"error\":\"Chart data unavailable\"}")]
    [InlineData(500, "{\"error\":\"Chart data unavailable\"}")]
    // HMDS throttle burst — body seen verbatim in production.
    [InlineData(503, "{\"error\":\"Service Unavailable\",\"statusCode\":503}")]
    [InlineData(400, "{\"error\":\"Service Unavailable\",\"statusCode\":503}")]
    // Any 5xx / 429 is transient regardless of body.
    [InlineData(502, "")]
    [InlineData(504, null)]
    [InlineData(429, "{\"error\":\"Too Many Requests\"}")]
    public void Transient_signatures_are_retryable(int status, string? body)
        => Assert.True(IBKRResponseParser.IsTransientHistoryError(status, body));

    [Theory]
    // Permanent errors: bad request shapes, unknown conid, auth handled
    // elsewhere (SendWithAuthAsync owns the 401 re-auth path).
    [InlineData(400, "{\"error\":\"Bad Request: invalid period\"}")]
    [InlineData(404, "{\"error\":\"no contract found\"}")]
    [InlineData(401, "{\"error\":\"not authenticated\"}")]
    [InlineData(403, "{\"error\":\"forbidden\"}")]
    [InlineData(400, "")]
    [InlineData(400, null)]
    public void Permanent_errors_are_not_retryable(int status, string? body)
        => Assert.False(IBKRResponseParser.IsTransientHistoryError(status, body));

    [Fact]
    public void Body_match_is_case_insensitive()
    {
        Assert.True(IBKRResponseParser.IsTransientHistoryError(
            400, "{\"error\":\"chart data unavailable\"}"));
        Assert.True(IBKRResponseParser.IsTransientHistoryError(
            400, "{\"error\":\"SERVICE UNAVAILABLE\"}"));
    }
}
