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

    /// <summary>
    /// The body of ONE endpoint handler, from its MapPost to its WithName.
    ///
    /// Ordering assertions have to be scoped to a single handler. This file
    /// holds several endpoints now, so a whole-file LastIndexOf answers a
    /// question about the LAST endpoint in the file rather than the one under
    /// test — which is how the check below silently stopped testing what it
    /// claimed to.
    /// </summary>
    private static string Handler(string path, string name)
    {
        var s = Src();
        var start = s.IndexOf($"MapPost(\"{path}\"", StringComparison.Ordinal);
        Assert.True(start > 0, $"endpoint {path} not found");
        var end = s.IndexOf($".WithName(\"{name}\")", start, StringComparison.Ordinal);
        Assert.True(end > start, $"WithName({name}) not found after {path}");
        return s[start..end];
    }

    [Fact]
    public void BothLegsAreResolvedBeforeEitherIsPlaced()
    {
        // Placing leg one and then failing to resolve leg two leaves a NAKED
        // short. Resolution must complete for both before any order goes out.
        //
        // ANCHORED ON THE REAL CALL. This previously looked for
        // "PlaceMarketOrderAsync", which no longer appears in any code here —
        // only inside the comment explaining why it was abandoned. The test was
        // asserting against prose, and reported the resulting position as a
        // failure without anyone learning what it meant.
        foreach (var (path, name) in new[]
                 {
                     ("/integrations/ibkr/strangle", "PlaceStrangle"),
                     ("/integrations/ibkr/strangle/close", "CloseStrangle"),
                 })
        {
            var h = Handler(path, name);
            var lastResolve = h.LastIndexOf("ResolveOptionConidAsync", StringComparison.Ordinal);
            var firstPlace = h.IndexOf("PlaceMarketOrderConfirmedAsync", StringComparison.Ordinal);
            Assert.True(lastResolve > 0 && firstPlace > 0,
                $"{name} must both resolve and place");
            Assert.True(lastResolve < firstPlace,
                $"{name}: both contracts must resolve before the first order is placed");
        }
        Assert.Contains("NOTHING was placed", Src());
    }

    [Fact]
    public void ABuyIsRefusedUnlessWeAreActuallyShortThatContract()
    {
        // Owner, 31 Aug 2026: "u shd be able to close them". The single-leg
        // close exists so a lone short put has an exit — but the same BUY that
        // closes a short OPENS A LONG when there is no short to close. The
        // paired endpoint would have made exactly that mistake on a put-only
        // book, so the position is verified at the broker before the order.
        var h = Handler("/integrations/ibkr/option-leg", "PlaceOptionLeg");
        var verify = h.IndexOf("GetPositionsAsync", StringComparison.Ordinal);
        var place = h.IndexOf("PlaceMarketOrderConfirmedAsync", StringComparison.Ordinal);
        Assert.True(verify > 0 && place > verify,
            "the held-position check must run BEFORE the order is placed");
        Assert.Contains("would OPEN A LONG", h);
        // Failing to READ positions must refuse, never assume it is a close.
        Assert.Contains("refusing to guess", h);
    }

    [Fact]
    public void AnIncompleteFlattenIsNeverReportedAsFlat()
    {
        // A sweep that closes three of four legs has left a NAKED short. This
        // desk has already been bitten by a cheerful summary over a partial
        // result, so the count must be reported and ok must be false.
        var h = Handler("/integrations/ibkr/options/flatten", "FlattenShortOptions");
        Assert.Contains("ok = failed == 0", h);
        Assert.Contains("STILL OPEN", h);
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
