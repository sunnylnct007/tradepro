using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Lane id → registry name, the lookup that decides RETIRED vs STALE.
///
/// WHY IT MATTERS. A lane is "&lt;registry name&gt;_&lt;broker&gt;":
/// momentum_pullback_t212 and momentum_pullback_ibkr are both the registered
/// strategy `momentum_pullback`. The registry knows nothing about brokers, so
/// the suffix comes off before asking it whether the strategy still exists.
///
/// Get this wrong in either direction and the panel lies:
///   * over-strip — mean_reversion_swing → mean_reversion, which the registry
///     does not know, so a LIVE sleeve renders as retired and a real outage on
///     it would never raise "needs attention";
///   * under-strip — a new broker suffix nobody listed, same outcome.
///
/// The second is the known hazard: BrokerSuffixes is a hand-maintained list,
/// and this desk's recurring infra failure is exactly that shape. It cannot be
/// inferred (splitting on the last underscore destroys mean_reversion_swing),
/// so it is pinned here against the ACTUAL live lane ids instead.
/// </summary>
public sealed class StrategyHealthBaseNameTest
{
    [Theory]
    // the three live lanes, 13 Oct 2026
    [InlineData("momentum_pullback_ibkr", "momentum_pullback")]
    [InlineData("momentum_pullback_t212", "momentum_pullback")]
    [InlineData("mean_reversion_swing_ibkr", "mean_reversion_swing")]
    // retired lanes — must also resolve, or they fall through to "stale"
    [InlineData("ichimoku_equity_ibkr", "ichimoku_equity")]
    [InlineData("ichimoku_fx_mr_ibkr", "ichimoku_fx_mr")]
    public void Strips_the_broker_suffix(string laneId, string expected)
        => Assert.Equal(expected, StrategyHealthEndpoints.BaseName(laneId));

    [Theory]
    // No broker suffix: these ARE registry names and must survive untouched.
    [InlineData("mean_reversion_swing")]
    [InlineData("momentum_pullback")]
    [InlineData("ichimoku_equity")]
    [InlineData("intraday_flat")]
    public void Leaves_a_bare_registry_name_alone(string name)
        => Assert.Equal(name, StrategyHealthEndpoints.BaseName(name));

    [Fact]
    public void Does_not_over_strip_mean_reversion_swing()
    {
        // The specific trap: "split on the last underscore" would yield
        // "mean_reversion", which the registry does not know — so the swing
        // sleeve, which traded 3 orders today, would render as RETIRED.
        Assert.Equal("mean_reversion_swing",
            StrategyHealthEndpoints.BaseName("mean_reversion_swing"));
        Assert.NotEqual("mean_reversion",
            StrategyHealthEndpoints.BaseName("mean_reversion_swing_ibkr"));
    }

    [Fact]
    public void Strips_only_one_suffix()
    {
        // Guards against a loop: "_ibkr_paper" must not reduce twice and lose
        // a real name component.
        Assert.Equal("foo_ibkr", StrategyHealthEndpoints.BaseName("foo_ibkr_paper"));
    }

    [Theory]
    [InlineData("")]
    [InlineData("_ibkr")]
    public void Degenerate_input_does_not_throw(string laneId)
        => Assert.NotNull(StrategyHealthEndpoints.BaseName(laneId));
}
