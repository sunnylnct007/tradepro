using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Limit orders — the prerequisite for a broker-side exit.
///
/// Owner, 1 Sep 2026: "so cant we have auto close while placing" / "thought we
/// could place profit taking and stop at time".
///
/// WHY IT MATTERS. The exit currently lives entirely in our scheduled job, so
/// it depends on the Lambda running, the IBKR session being alive, and the
/// option chain resolving. All three failed at some point on 1 Sep. A profit
/// target that RESTS AT THE BROKER survives all of them.
///
/// The order payload was hardcoded to a DAY MARKET order — right for entering,
/// wrong for exiting.
/// </summary>
public class LimitOrderSupportTest
{
    private static string Src(string file)
    {
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null
               && !Directory.Exists(Path.Combine(d.FullName, ".git"))
               && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        Assert.NotNull(d);
        return File.ReadAllText(Path.Combine(d!.FullName, file));
    }

    private const string Client = "backend/TradePro.Api/Providers/IBKR/IBKRClient.cs";

    [Fact]
    public void ALimitOrderWithNoPriceIsRefusedNotDowngraded()
    {
        // Silently sending a LMT with no price as a MARKET order would exit at
        // whatever happens to be there — the single worst outcome for a short
        // option, where the ask can be far from the mid.
        var s = Src(Client);
        Assert.Contains("a LMT order needs a price", s);
        Assert.Contains("refusing to send it as a market order", s);
    }

    [Fact]
    public void TheOrderTypeIsNoLongerHardcodedInThePayload()
    {
        // SCOPED TO THE PAYLOAD. A bare DoesNotContain matched the method's own
        // PARAMETER DEFAULT (`string orderType = "MKT",`) — which is exactly
        // what we want to keep. Three source-text assertions misfired this way
        // today; the lesson is that they must be scoped to the code they mean.
        var s = Src(Client);
        var i = s.IndexOf("orders = new[]", StringComparison.Ordinal);
        Assert.True(i > 0, "order payload not found");
        var payload = s[i..(i + 700)];
        Assert.Contains("orderType = type,", payload);
        Assert.DoesNotContain("orderType = \"MKT\",", payload);
    }

    [Fact]
    public void MarketRemainsTheDefaultSoEntryBehaviourIsUnchanged()
    {
        // Every existing caller passes no order type. Entry must keep working
        // exactly as before this change.
        var s = Src(Client);
        Assert.Contains("string orderType = \"MKT\", decimal? price = null, string tif = \"DAY\"", s);
        Assert.Contains("string.IsNullOrWhiteSpace(orderType) ? \"MKT\"", s);
    }
}
