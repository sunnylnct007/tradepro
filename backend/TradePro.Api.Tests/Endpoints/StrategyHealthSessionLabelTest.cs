using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>Session label -> strategy id, the join that decides RUNNING vs STOPPED.
///
/// WHY IT EXISTS. The panel used to classify purely on "has it placed an order
/// recently", which lies in two ways seen live on 16 Oct 2026:
///   * momentum_pullback_ibkr is in WIND-DOWN — it places exits only, by
///     design, so it would read "stale" every day forever;
///   * swing fires on ~31% of sessions, so a dry 33h is the rule working.
/// Both reported "registered, so it SHOULD be trading", which is false.
///
/// The honest signal is the per-pass snapshot heartbeat, keyed by session label.
/// If this parsing is wrong the heartbeat never matches the strategy and the
/// panel silently falls back to the lying behaviour — so it is pinned here.
/// </summary>
public sealed class StrategyHealthSessionLabelTest
{
    [Theory]
    [InlineData("momentum_pullback_ibkr-2026-10-16", "momentum_pullback_ibkr")]
    [InlineData("mean_reversion_swing_ibkr-2026-10-16", "mean_reversion_swing_ibkr")]
    [InlineData("momentum_pullback_t212-2026-10-16", "momentum_pullback_t212")]
    public void Strips_the_trailing_date(string label, string expected)
        => Assert.Equal(expected, StrategyHealthEndpoints.StrategyIdFromSessionLabel(label));

    [Fact]
    public void Keeps_dashes_that_belong_to_the_strategy_id()
    {
        // The trap: splitting on the FIRST dash reduces every per-symbol engine
        // id to "intraday", merging hundreds of them into one phantom sleeve.
        Assert.Equal("intraday-ichimoku_equity-AAPL",
            StrategyHealthEndpoints.StrategyIdFromSessionLabel(
                "intraday-ichimoku_equity-AAPL-2026-10-16"));
    }

    [Theory]
    [InlineData("")]
    [InlineData("nodate")]
    [InlineData("-")]
    public void Degenerate_labels_do_not_throw(string label)
        => Assert.NotNull(StrategyHealthEndpoints.StrategyIdFromSessionLabel(label));

    [Fact]
    public void A_label_with_no_date_is_returned_unchanged()
        => Assert.Equal("nodate",
            StrategyHealthEndpoints.StrategyIdFromSessionLabel("nodate"));
}
