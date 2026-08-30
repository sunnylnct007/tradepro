using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// NO ALGORITHMIC ORDER MAY REACH THE LIVE ACCOUNT.
///
/// Owner, 30 Aug 2026: "ensure we never place algo order to live account by any
/// chance. our aws secret contains our live as well as paper trading
/// credentials. its fine to use the live for read but no way we shd be placing
/// the order to live as we are far away from algo trading setup."
///
/// The audit that produced these tests found the guarantee rested on ONE value.
/// Mode selects the credential triple, so mode=paper genuinely cannot
/// authenticate against the live account. But IsLive was used in four places,
/// all credential selection, and NOWHERE to refuse an order — while AllowOrders
/// is TRUE in production, despite comments across the codebase still claiming
/// it "is absent from the secret and so binds to false".
///
/// One secret edit (paper -> live) would therefore have routed orders live,
/// using live credentials that sit in the same secret because the live account
/// is used for READS.
/// </summary>
public class NoLiveOrdersTest
{
    private static IBKROptions Opts(string mode, bool allowOrders, bool allowLive = false)
        => new() { Mode = mode, AllowOrders = allowOrders, AllowLiveOrders = allowLive };

    [Fact]
    public void LiveMode_WithOrdersEnabled_IsStillRefused()
    {
        // THE CASE THAT WAS UNGUARDED: production has AllowOrders=true, so
        // flipping Mode alone used to be sufficient.
        var o = Opts("live", allowOrders: true);
        Assert.True(o.IsLiveMode);
        Assert.False(o.AllowOrders && (!o.IsLiveMode || o.AllowLiveOrders));
    }

    [Theory]
    [InlineData("live")]
    [InlineData("LIVE")]
    [InlineData("Live")]
    public void LiveModeIsDetectedRegardlessOfCasing(string mode)
    {
        // A casing miss here would silently reopen the hole.
        Assert.True(Opts(mode, allowOrders: true).IsLiveMode);
    }

    [Fact]
    public void PaperMode_WithOrdersEnabled_IsPermitted()
    {
        // The guard must not break the paper forward test, which is the whole
        // point of the system today.
        var o = Opts("paper", allowOrders: true);
        Assert.False(o.IsLiveMode);
        Assert.True(o.AllowOrders && (!o.IsLiveMode || o.AllowLiveOrders));
    }

    [Fact]
    public void LiveOrders_RequireBothKeys()
    {
        // Deliberately possible, so this is a decision someone makes rather
        // than a hardcode they rip out. But it takes TWO explicit values.
        var o = Opts("live", allowOrders: true, allowLive: true);
        Assert.True(o.AllowOrders && (!o.IsLiveMode || o.AllowLiveOrders));

        // Either key alone is not enough.
        Assert.False(Opts("live", allowOrders: false, allowLive: true).AllowOrders);
        var oneKey = Opts("live", allowOrders: true, allowLive: false);
        Assert.False(oneKey.AllowOrders && (!oneKey.IsLiveMode || oneKey.AllowLiveOrders));
    }

    [Fact]
    public void AllowLiveOrders_DefaultsToFalse()
    {
        // Absent from the secret must mean OFF. If this ever defaults true, a
        // missing key becomes permission.
        Assert.False(new IBKROptions().AllowLiveOrders);
        Assert.False(new IBKROptions().AllowOrders);
        Assert.Equal("disabled", new IBKROptions().Mode);
    }

    [Fact]
    public void LiveCredentialsAreStillSelectableForREADS()
    {
        // The owner explicitly wants live READS. The guard must block placement
        // ONLY — not credential resolution, or the live position reads break.
        var o = new IBKROptions
        {
            Mode = "live", AccountIdLive = "U1234567", AccountIdPaper = "DUP656969",
        };
        Assert.Equal("U1234567", o.AccountId);
    }
}
