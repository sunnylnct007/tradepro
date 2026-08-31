using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Option orders must use the CONFIRMED placement path, like stock orders do.
///
/// 31 Aug 2026: the strangle endpoint called PlaceMarketOrderAsync, which stops
/// at IBKR's NEEDS_CONFIRM precaution prompt. Both legs reached IBKR and neither
/// placed. I then wrote a bespoke confirmation loop beside the one that already
/// existed.
///
/// The owner spotted it in one line — "but we are able to place stock orders".
/// The OMS has driven the full place -> reply/confirm -> real order id sequence
/// for months via PlaceMarketOrderConfirmedAsync, and IBKRClient's own comment
/// records why: the unconfirmed path "persisted the REPLY id as broker_order_id
/// while the order never actually placed — the exact reason the clone's fills
/// carried no broker id".
/// </summary>
public class StrangleUsesConfirmedPlaceTest
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
    public void OptionsUseTheSameConfirmedPathAsStocks()
    {
        var s = Src();
        Assert.Contains("PlaceMarketOrderConfirmedAsync", s);
    }

    [Fact]
    public void TheUnconfirmedCallIsNotUsed()
    {
        // PlaceMarketOrderAsync stops at NEEDS_CONFIRM. Using it here is how
        // both legs reached IBKR and neither placed.
        var s = Src();
        Assert.DoesNotContain("ibkr.PlaceMarketOrderAsync(", s);
    }

    [Fact]
    public void NoBespokeConfirmationLoopBesideTheWorkingOne()
    {
        // Re-solving a solved problem is the failure mode here, not the bug.
        var s = Src();
        Assert.DoesNotContain("PlaceAndConfirmAsync", s);
        Assert.DoesNotContain("ConfirmReplyAsync", s);
    }
}
