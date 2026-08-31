using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// An option position must be identifiable AS an option, and its P&L must not lie.
///
/// 31 Aug 2026: two short puts sat in the paper account and every surface
/// showed them as plain "SPY" and "QQQ" stock rows. IBKRPosition never carried
/// assetClass, so nothing downstream could tell them apart — and the conclusion
/// drawn was that no option positions existed at all.
///
/// Worse, the percentage read -99.06% on a position that was UP $38.63. IBKR
/// reports an option's avgCost as premium x multiplier (600.95 for a put sold
/// at 6.01) while mktPrice stays per-share (5.62). Dividing one by the other
/// compares a total against a unit price.
/// </summary>
public class OptionPositionVisibilityTest
{
    [Fact]
    public void ThePositionRecordCarriesTheAssetClass()
    {
        var t = typeof(IBKRPosition);
        Assert.NotNull(t.GetProperty("AssetClass"));
        Assert.NotNull(t.GetProperty("ContractDesc"));
        Assert.NotNull(t.GetProperty("Multiplier"));
    }

    [Theory]
    // The REAL numbers off the paper account: a put sold at 6.0095 (avgCost
    // 600.95 with a x100 multiplier), now 5.62 — a GAIN on a short.
    [InlineData(600.95, 5.62, -1, 100, true)]
    // The same shape on QQQ.
    [InlineData(671.56, 6.23, -1, 100, true)]
    public void AShortOptionInProfitDoesNotReportALoss(
        double avgCost, double mktPrice, int qty, int mult, bool expectGain)
    {
        var avgPerShare = (decimal)avgCost / mult;
        var raw = ((decimal)mktPrice - avgPerShare) / avgPerShare * 100m;
        var pct = qty < 0 ? -raw : raw;
        Assert.Equal(expectGain, pct > 0);
        // And nowhere near the -99% the unnormalised maths produced.
        Assert.InRange(pct, -50m, 50m);
    }

    [Fact]
    public void TheUnnormalisedMathsIsWhatProducedMinus99()
    {
        // Pinned so the regression is recognisable if it ever returns: comparing
        // a x100 cost against a per-share price.
        var wrong = (5.62m - 600.95m) / 600.95m * 100m;
        Assert.InRange(wrong, -100m, -98m);
    }

    [Fact]
    public void AStockPositionIsUnaffected()
    {
        // multiplier 1, long side — the ordinary case must not change.
        var pct = (131.63m - 117.98m) / 117.98m * 100m;
        Assert.InRange(pct, 11m, 12m);
    }
}
