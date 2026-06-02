using TradePro.Api.Oms;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// Regression coverage for the manual-SELL 404 bug: a bare ticker ("IBM")
/// enqueued via POST /api/oms/orders reached T212 unharmonised and was
/// rejected with HTTP 404 entity-not-found — a silently dropped SELL the
/// operator could mistake for a flat position. Harmonisation now runs at the
/// placement boundary; it must convert bare tickers AND be idempotent for the
/// already-suffixed symbols the strategy path produces.
/// </summary>
public class SymbolHarmonizationTest
{
    [Theory]
    [InlineData("IBM", "IBM_US_EQ")]
    [InlineData("now", "NOW_US_EQ")]   // case-insensitive
    [InlineData(" SNOW ", "SNOW_US_EQ")]  // trims whitespace
    public void T212_bare_ticker_gets_us_eq_suffix(string input, string expected)
        => Assert.Equal(expected, SymbolHarmonization.ToBrokerTicker(input, "T212_DEMO"));

    [Theory]
    [InlineData("AAPL_US_EQ")]
    [InlineData("GLD_US_EQ")]
    public void T212_already_harmonised_is_idempotent(string already)
        => Assert.Equal(already, SymbolHarmonization.ToBrokerTicker(already, "T212_DEMO"));

    [Fact]
    public void IG_strips_t212_suffix_back_to_bare()
        => Assert.Equal("AAPL", SymbolHarmonization.ToBrokerTicker("AAPL_US_EQ", "IG_DEMO"));

    [Fact]
    public void IG_bare_ticker_passes_through()
        => Assert.Equal("AAPL", SymbolHarmonization.ToBrokerTicker("aapl", "IG_LIVE"));

    [Fact]
    public void Unknown_broker_returns_uppercased_input()
        => Assert.Equal("FOO", SymbolHarmonization.ToBrokerTicker("foo", "PAPER"));
}
