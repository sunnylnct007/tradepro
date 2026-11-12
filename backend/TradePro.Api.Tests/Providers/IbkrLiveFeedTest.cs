using System.Text.Json;
using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The ONE rule for "is IBKR serving live prices". The strangle P&amp;L alarm and
/// the quote endpoint both use it, so a change here changes both on purpose.
/// Shapes are IBKR's own snapshot bodies, including the 12 Nov 2026 dark one.
/// </summary>
public sealed class IbkrLiveFeedTest
{
    private static JsonElement El(string json) => JsonDocument.Parse(json).RootElement.Clone();

    [Theory]
    [InlineData("{\"conid\":756733,\"31\":\"762.15\"}")]
    [InlineData("{\"conid\":756733,\"84\":\"762.10\",\"86\":\"762.20\"}")]
    public void A_real_last_bid_or_ask_is_live(string json)
        => Assert.Equal(LiveFeedState.Live, IbkrLiveFeed.Classify(El(json)));

    [Fact]
    public void A_C_prefixed_last_is_the_previous_close_not_a_fault()
        => Assert.Equal(LiveFeedState.Closed, IbkrLiveFeed.Classify(El("{\"conid\":1,\"31\":\"C761.83\"}")));

    [Fact]
    public void An_H_prefixed_last_is_halted()
        => Assert.Equal(LiveFeedState.Halted, IbkrLiveFeed.Classify(El("{\"conid\":1,\"31\":\"H12.50\"}")));

    [Fact]
    public void The_12_Nov_dark_body_is_dark()
    {
        // Captured live 12 Nov 2026 18:10Z: contract resolved, IV field "N/A", no price at all.
        var dark = "{\"conidEx\":\"756733\",\"conid\":756733,\"_updated\":1794506912641,\"6119\":\"q0\",\"server_id\":\"q0\",\"7283\":\"N/A\"}";
        Assert.Equal(LiveFeedState.Dark, IbkrLiveFeed.Classify(El(dark)));
    }

    [Theory]
    [InlineData(null, true)]
    [InlineData("", true)]
    [InlineData("[{\"conid\":756733,\"conidEx\":\"756733\"}]", true)]
    [InlineData("[{\"conid\":756733,\"31\":\"762.15\"}]", false)]
    [InlineData("[{\"conid\":756733,\"7283\":\"N/A\"}]", false)]
    public void Cold_snapshot_is_only_identifiers(string? raw, bool cold)
        => Assert.Equal(cold, IbkrLiveFeed.IsColdSnapshot(raw));
}
