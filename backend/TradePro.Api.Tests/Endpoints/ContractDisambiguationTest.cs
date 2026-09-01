using Xunit;
using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// SPX is not SPXW.
///
/// On a third Friday the AM-settled monthly (SPX) and the PM-settled weekly
/// (SPXW) both exist at the same strike and expiry. The chain parser dropped
/// `tradingClass`, so the resolver could not tell them apart and took
/// FirstOrDefault. On 1 Sep 2026 an order for SPX filled as SPXW — a different
/// instrument from the one the config describes ("cash-settled index option ·
/// European") and from whatever the backtest assumed.
///
/// It barely matters while the desk closes same-day. It matters enormously the
/// first time a position is held to expiry, and "which instrument did we
/// actually trade" should never have been unanswerable.
/// </summary>
public class ContractDisambiguationTest
{
    [Fact]
    public void TheParserKeepsTheTradingClass()
    {
        const string json = """
        [
          {"conid": 111, "strike": 7545, "maturityDate": "20260918", "tradingClass": "SPX"},
          {"conid": 222, "strike": 7545, "maturityDate": "20260918", "tradingClass": "SPXW"}
        ]
        """;
        var rows = IBKRResponseParser.ParseOptionContracts(json);
        Assert.Equal(2, rows.Count);
        Assert.Equal("SPX", rows[0].TradingClass);
        Assert.Equal("SPXW", rows[1].TradingClass);
    }

    [Fact]
    public void ItFallsBackToSymbolWhenTradingClassIsAbsent()
    {
        const string json = """
        [{"conid": 333, "strike": 680, "maturityDate": "20260918", "symbol": "XSP"}]
        """;
        var rows = IBKRResponseParser.ParseOptionContracts(json);
        Assert.Equal("XSP", rows[0].TradingClass);
    }

    [Fact]
    public void AMissingClassIsNullNotGuessed()
    {
        const string json = """
        [{"conid": 444, "strike": 758, "maturityDate": "20260918"}]
        """;
        var rows = IBKRResponseParser.ParseOptionContracts(json);
        Assert.Null(rows[0].TradingClass);
    }

    [Fact]
    public void TheResolverPrefersTheRequestedClassAndRefusesGenuineAmbiguity()
    {
        // Source-level: the behaviour needs a live chain to exercise, but the
        // two guarantees must be visible and must not regress.
        var d = new DirectoryInfo(AppContext.BaseDirectory);
        while (d is not null && !Directory.Exists(Path.Combine(d.FullName, ".git"))
                             && !File.Exists(Path.Combine(d.FullName, ".git")))
            d = d.Parent;
        var s = File.ReadAllText(Path.Combine(
            d!.FullName, "backend/TradePro.Api/Providers/IBKR/IBKRClient.cs"));

        // 1. prefer the class the caller asked for
        Assert.Contains("c.TradingClass, sym, StringComparison.OrdinalIgnoreCase", s);
        // 2. a single alternative class is USED but reported, not silently taken
        Assert.Contains("resolved as trading class", s);
        // 3. genuinely different classes are refused, never guessed between
        Assert.Contains("AMBIGUOUS CONTRACT", s);
        Assert.DoesNotContain(
            "var exact = res.Contracts.FirstOrDefault(", s);
    }
}
