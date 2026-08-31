using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Paper strangle execution — the guards that must never be optional.
///
/// Owner: "ok start with the us paper execution". This is the piece that turns
/// modelled Black-Scholes credits into REAL fills, which is the one input no
/// backtest can manufacture. It is also the first thing in this project that
/// sends option orders anywhere, so the guards matter more than the feature.
/// </summary>
public class StrangleOrderGuardTest
{
    private static string Src()
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null
               && !Directory.Exists(Path.Combine(d.FullName, ".git"))
               && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        Assert.NotNull(d);
        return File.ReadAllText(Path.Combine(d!.FullName, "backend", "TradePro.Api",
            "Endpoints", "StrangleOrderEndpoints.cs"));
    }

    [Fact]
    public void LiveAccountsAreRefusedBeforeAnythingIsResolved()
    {
        var s = Src();
        var guard = s.IndexOf("ibkr.AllowOrders", StringComparison.Ordinal);
        var resolve = s.IndexOf("ResolveOptionConidAsync", StringComparison.Ordinal);
        Assert.True(guard > 0 && resolve > guard,
            "the AllowOrders/live guard must come BEFORE any contract resolution");
        Assert.Contains("blockedForLive", s);
    }

    [Fact]
    public void BothLegsAreResolvedBeforeEitherIsPlaced()
    {
        // Placing leg one and then failing to resolve leg two leaves a NAKED
        // short. Resolution must complete for both before any order goes out.
        var s = Src();
        var lastResolve = s.LastIndexOf("ResolveOptionConidAsync", StringComparison.Ordinal);
        var firstPlace = s.IndexOf("PlaceMarketOrderAsync", StringComparison.Ordinal);
        Assert.True(lastResolve < firstPlace,
            "both contracts must resolve before the first order is placed");
        Assert.Contains("NOTHING was placed", s);
    }

    [Fact]
    public void APartialFillIsReportedAsNakedNotAsSuccess()
    {
        // A strangle with one leg filled is a naked short — a different and
        // much worse trade. It must never be summarised cheerfully.
        var s = Src();
        Assert.Contains("partial", s);
        Assert.Contains("NAKED", s);
        Assert.Contains("putOk ^ callOk", s);
    }

    [Fact]
    public void ACrossedStrangleIsRejected()
    {
        var s = Src();
        Assert.Contains("req.PutStrike >= req.CallStrike", s);
    }

    [Fact]
    public void TheModelledCreditIsNotPresentedAsEvidence()
    {
        // The entire reason for placing paper orders is that the Black-Scholes
        // credit is NOT evidence. The response must say so.
        var s = Src();
        Assert.Contains("whatever the broker actually filled", s);
    }
}
