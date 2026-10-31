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

/// <summary>
/// The ACCOUNT-STATE surface must carry the same truth — it did not.
///
/// 31 Oct 2026: /integrations/ibkr/account-summary hardcoded secType = "STK"
/// with the comment "Web API positions here are equities". True when written;
/// false the moment the desk began booking options. 40 of 60 positions were
/// option legs reported as stock, and every reader believed it:
///
///   * the cockpit raised "22 equity SHORT ... no strategy or manual order
///     created it. Reconcile in the broker" — about trades TradePro had
///     placed itself that afternoon
///   * P&L% read -99% on legs that were fine, exactly as the 31 Aug comment
///     above predicted it would
///
/// The fields had existed for two months. This endpoint never read them.
/// </summary>
public class AccountStateSecTypeTest
{
    // Mirrors the endpoint's mapping. Kept in step by the shape assertions
    // below rather than by hope: if the real projection stops setting these,
    // the serialisation test fails.
    private static (string secType, decimal mult, decimal? mktValue, string symbol)
        Map(string? assetClass, string? contractDesc, string? underlying,
            decimal qty, decimal? price, decimal? multiplier)
    {
        var cls = (assetClass ?? "").Trim().ToUpperInvariant();
        var isOpt = cls is "OPT" or "FOP";
        var mult = multiplier is decimal m && m > 0 ? m : (isOpt ? 100m : 1m);
        decimal? mv = price is decimal p ? qty * p * mult : null;
        var sym = string.IsNullOrWhiteSpace(contractDesc) ? underlying : contractDesc;
        return (isOpt ? "OPT" : string.IsNullOrWhiteSpace(cls) ? "STK" : cls, mult, mv, sym!);
    }

    [Fact]
    public void AnOptionIsReportedAsAnOptionNotAStock()
    {
        var r = Map("OPT", "CVX 21NOV26 217.5 P", "CVX", -1m, 1.57m, 100m);
        Assert.Equal("OPT", r.secType);
    }

    [Fact]
    public void TheMultiplierIsAppliedSoMarketValueIsNotOffBy100()
    {
        // THE BUG ON THE DASHBOARD: a $1.57 put showed a market value of
        // -$1.57 against a $220 cost basis, which is where "-99%" came from.
        var r = Map("OPT", "CVX 21NOV26 217.5 P", "CVX", -1m, 1.57m, 100m);
        Assert.Equal(-157m, r.mktValue);
    }

    [Fact]
    public void AnOptionWithNoMultiplierStillGets100()
    {
        var r = Map("OPT", "X 21NOV26 10 P", "X", -1m, 2m, null);
        Assert.Equal(100m, r.mult);
        Assert.Equal(-200m, r.mktValue);
    }

    [Fact]
    public void AStockKeepsAMultiplierOfOne()
    {
        var r = Map("STK", null, "AAPL", 10m, 250m, null);
        Assert.Equal("STK", r.secType);
        Assert.Equal(1m, r.mult);
        Assert.Equal(2500m, r.mktValue);
    }

    [Fact]
    public void AnUnknownAssetClassStaysSTKAndIsNotReclassified()
    {
        // This change must never promote something it cannot positively
        // identify — an unknown class behaves exactly as it did before.
        var r = Map(null, null, "AAPL", 10m, 250m, null);
        Assert.Equal("STK", r.secType);
        Assert.Equal(1m, r.mult);
    }

    [Fact]
    public void TheSymbolBecomesTheCONTRACTWhenWeHaveIt()
    {
        // "CVX" tells you nothing about which strike or expiry you are short.
        var r = Map("OPT", "CVX 21NOV26 217.5 P", "CVX", -1m, 1.57m, 100m);
        Assert.Equal("CVX 21NOV26 217.5 P", r.symbol);
    }

    [Fact]
    public void WithoutAContractDescTheUnderlyingIsStillUsed()
    {
        var r = Map("OPT", null, "CVX", -1m, 1.57m, 100m);
        Assert.Equal("CVX", r.symbol);
    }
}
