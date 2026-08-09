using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Route-B fail-loud coverage (8 Aug 2026 finding): a chain whose EVERY leg
/// is quote-less (bid/ask/delta all null — cold IBKR quote cache or market
/// closed) must be flagged as an error, never flow downstream as a "success"
/// that consumers zero-fill into fabricated $0-premium / 0-IV quotes.
/// </summary>
public class ChainQuotelessTest
{
    private static ChainLeg Leg(
        decimal strike, string right = "P",
        decimal? bid = null, decimal? ask = null, decimal? delta = null)
        => new(
            ConId: 1, Strike: strike, Right: right,
            Bid: bid, Ask: ask, Last: null,
            Delta: delta, Gamma: null, Theta: null, Vega: null,
            ImpliedVolPct: null, OpenInterest: null);

    [Fact]
    public void All_null_legs_are_quoteless()
    {
        var legs = new[] { Leg(100), Leg(105), Leg(110) };
        Assert.True(ChainEndpoints.AllLegsQuoteless(legs));
    }

    [Fact]
    public void One_leg_with_a_bid_makes_the_chain_warm()
    {
        var legs = new[] { Leg(100), Leg(105, bid: 1.25m), Leg(110) };
        Assert.False(ChainEndpoints.AllLegsQuoteless(legs));
    }

    [Fact]
    public void One_leg_with_only_delta_makes_the_chain_warm()
    {
        // Market closed can leave bid/ask null while tick greeks persist —
        // that's a usable (if degraded) chain, not a cold one.
        var legs = new[] { Leg(100, delta: -0.31m), Leg(105) };
        Assert.False(ChainEndpoints.AllLegsQuoteless(legs));
    }

    [Fact]
    public void One_leg_with_only_an_ask_makes_the_chain_warm()
    {
        // Deep OTM legitimately has no bid; an ask alone is real market data.
        var legs = new[] { Leg(100, ask: 0.05m), Leg(105) };
        Assert.False(ChainEndpoints.AllLegsQuoteless(legs));
    }

    [Fact]
    public void Empty_chain_is_not_quoteless()
    {
        // Empty legs are a DIFFERENT failure (no contracts resolved) with its
        // own error path — the quote-less flag must not mask it.
        Assert.False(ChainEndpoints.AllLegsQuoteless(System.Array.Empty<ChainLeg>()));
    }
}
