using TradePro.Api.Oms;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// A broker's bare ticker must become the symbol the OMS actually stores.
///
/// THE BUG, 5 Oct 2026. The IBKR reconcile wrote `SELL MET` while every
/// strategy order for the same holding was `BUY MET_US_EQ`. To the OMS — and
/// to the RiskGate's oversell guard, which groups by symbol — those are two
/// unrelated instruments. The reconcile reported "adjusted: 4" and changed
/// nothing the guard could see.
///
/// It is the symbol-harmonisation mismatch this codebase keeps rediscovering,
/// so it gets a test rather than a comment.
/// </summary>
public class SymbolHarmonizationOmsSymbolTest
{
    [Theory]
    [InlineData("MET", "MET_US_EQ")]
    [InlineData("AAPL", "AAPL_US_EQ")]
    [InlineData("esnt", "ESNT_US_EQ")]      // case is normalised
    [InlineData("  CLF  ", "CLF_US_EQ")]    // and whitespace
    public void A_bare_broker_ticker_becomes_the_OMS_symbol(string input, string expected)
    {
        Assert.Equal(expected, SymbolHarmonization.ToOmsSymbol(input));
    }

    [Theory]
    [InlineData("MET_US_EQ")]
    [InlineData("AAPL_US_EQ")]
    public void An_already_harmonised_symbol_is_unchanged(string symbol)
    {
        // Idempotence matters: the reconcile runs repeatedly and a second pass
        // must not produce MET_US_EQ_US_EQ.
        Assert.Equal(symbol, SymbolHarmonization.ToOmsSymbol(symbol));
        Assert.Equal(symbol, SymbolHarmonization.ToOmsSymbol(
            SymbolHarmonization.ToOmsSymbol(symbol)));
    }

    [Fact]
    public void Empty_stays_empty_rather_than_becoming_a_suffix()
    {
        // "_US_EQ" as a symbol would be a silent garbage row in the ledger.
        Assert.Equal("", SymbolHarmonization.ToOmsSymbol(""));
        Assert.Equal("", SymbolHarmonization.ToOmsSymbol("   "));
    }

    [Fact]
    public void The_bug_itself_cannot_recur()
    {
        // The broker said "MET"; the ledger holds "MET_US_EQ". After
        // conversion they are the SAME key, so a reconcile adjustment nets
        // against the position it is correcting.
        const string fromBroker = "MET";
        const string inLedger = "MET_US_EQ";
        Assert.Equal(inLedger, SymbolHarmonization.ToOmsSymbol(fromBroker));
    }
}
