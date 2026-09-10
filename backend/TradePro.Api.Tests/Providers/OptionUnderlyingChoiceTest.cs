using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// Picking the OPTIONS underlying out of a symbol search.
///
/// 10 Sep 2026: GOLD had failed to place on every session since it was added,
/// always reported as "GLD has no listed option chain". GLD is one of the most
/// liquid ETF option chains there is. We had resolved the wrong instrument:
/// secdef/search returns Hong Kong "Gold Futures" (an IND, no options) under
/// the same ticker, and the parser took the first row it saw.
/// </summary>
public class OptionUnderlyingChoiceTest
{
    // Shaped like the real secdef/search reply, wrong row first.
    private const string GldSearch = """
    [
      {"conid":54927692,"symbol":"GLD","description":"Gold Futures",
       "sections":[{"secType":"IND"}]},
      {"conid":51529211,"symbol":"GLD","description":"SPDR GOLD SHARES",
       "sections":[{"secType":"STK"},{"secType":"OPT","months":"SEP26;OCT26;NOV26"}]}
    ]
    """;

    [Fact]
    public void TheUnderlyingIsTheOneThatActuallyHasOptions()
    {
        var (conId, months) = IBKRResponseParser.ParseOptionMonths(GldSearch);
        Assert.Equal(51529211L, conId);
        Assert.Contains("SEP26", months);
    }

    [Fact]
    public void AFirstRowWithNoOptionsIsNotChosen()
    {
        var (conId, _) = IBKRResponseParser.ParseOptionMonths(GldSearch);
        Assert.NotEqual(54927692L, conId);
    }

    [Fact]
    public void WhenNothingHasOptionsTheErrorStillNamesAContract()
    {
        // An error naming no instrument is what made this take five sessions
        // to find: "has no listed option chain" was true of the contract we
        // looked at, and said nothing about which one that was.
        const string noneHaveOptions = """
        [{"conid":54927692,"symbol":"GLD","sections":[{"secType":"IND"}]}]
        """;
        var (conId, months) = IBKRResponseParser.ParseOptionMonths(noneHaveOptions);
        Assert.Equal(54927692L, conId);
        Assert.Empty(months);
    }
}
