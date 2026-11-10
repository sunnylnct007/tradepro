using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// 10 Nov 2026: a 38-day spread on AMD was priced off NOV26 because the chain
/// endpoint took the FIRST listed month and only then matched targetDte inside
/// it. The month must come from the tenor.
/// </summary>
public class ChainMonthPickTest
{
    private static readonly string[] Months = { "NOV26", "DEC26", "JAN27", "MAR27" };

    [Fact]
    public void A_38_day_tenor_on_10_Nov_picks_December_not_November()
    {
        var m = ChainEndpoints.PickMonth(Months, new DateOnly(2026, 11, 10), 38);
        Assert.Equal("DEC26", m);
    }

    [Fact]
    public void A_short_tenor_still_picks_the_front_month()
    {
        Assert.Equal("NOV26", ChainEndpoints.PickMonth(Months, new DateOnly(2026, 11, 10), 7));
    }

    [Fact]
    public void No_tenor_keeps_the_legacy_first_month()
    {
        Assert.Equal("NOV26", ChainEndpoints.PickMonth(Months, new DateOnly(2026, 11, 10), null));
    }

    [Fact]
    public void Third_friday_is_computed_from_the_month_token()
    {
        Assert.Equal(new DateOnly(2026, 12, 18), ChainEndpoints.ThirdFriday("DEC26"));
        Assert.Equal(new DateOnly(2026, 11, 20), ChainEndpoints.ThirdFriday("NOV26"));
        Assert.Null(ChainEndpoints.ThirdFriday("garbage"));
    }

    [Fact]
    public void Unparseable_months_fall_back_to_the_first_listed()
    {
        Assert.Equal("??", ChainEndpoints.PickMonth(new[] { "??", "!!" }, new DateOnly(2026, 11, 10), 38));
    }
}
