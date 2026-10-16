using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>Trading classes read from secdef/search — the last-resort key for
/// resolving an option contract.
///
/// WHY. From 5 to 16 Oct 2026 the strangle could not place. Measured with the
/// market open, resolution succeeded ONLY for (strike, right) pairs that had
/// resolved once before and been cached, and failed for every newly computed
/// one — so a job whose strikes move daily almost never hit.
///
/// This is the third time an UNDER-SPECIFIED secdef/info has produced that
/// symptom (2 Sep 2026: exchange and sectype missing, 52 of 56 strikes
/// failing). tradingClass is the remaining unnamed parameter, and XSP really
/// does list two (XSP monthly, XSPW weekly).
///
/// The classes are DERIVED from the response already fetched, never from a
/// list in code — the roots live in the Python market config and copying them
/// across the boundary is the hand-maintained-list shape that has already
/// misdescribed three surfaces here.
/// </summary>
public sealed class OptionTradingClassParseTest
{
    private const string XspSearch = """
    [
      {"conid":137851301,"symbol":"XSP","description":"S&P 500 MINI SPX",
       "sections":[
         {"secType":"IND"},
         {"secType":"OPT","months":"OCT26;NOV26","symbol":"XSP","exchange":"SMART"},
         {"secType":"OPT","months":"OCT26;NOV26","symbol":"XSPW","exchange":"SMART"}
       ]}
    ]
    """;

    [Fact]
    public void Finds_both_classes_for_XSP()
    {
        var cls = IBKRResponseParser.ParseOptionTradingClasses(XspSearch, 137851301);
        Assert.Equal(new[] { "XSP", "XSPW" }, cls);
    }

    [Fact]
    public void Ignores_rows_for_a_DIFFERENT_contract_sharing_the_ticker()
    {
        // The GLD trap, which cost five sessions: secdef/search returns a Hong
        // Kong gold FUTURES index alongside the ETF. Its sections must never
        // contribute trading classes to the instrument we resolved.
        const string json = """
        [
          {"conid":54927692,"symbol":"GLD","description":"Gold Futures",
           "sections":[{"secType":"OPT","symbol":"HKGOLD"}]},
          {"conid":51529211,"symbol":"GLD","description":"SPDR GOLD SHARES",
           "sections":[{"secType":"OPT","symbol":"GLD"}]}
        ]
        """;
        Assert.Equal(new[] { "GLD" },
            IBKRResponseParser.ParseOptionTradingClasses(json, 51529211));
    }

    [Fact]
    public void Falls_back_to_the_rows_own_symbol_when_the_section_has_none()
    {
        const string json = """
        [{"conid":1,"symbol":"SPY","sections":[{"secType":"OPT","months":"OCT26"}]}]
        """;
        Assert.Equal(new[] { "SPY" }, IBKRResponseParser.ParseOptionTradingClasses(json, 1));
    }

    [Fact]
    public void Non_option_sections_contribute_nothing()
    {
        const string json = """
        [{"conid":1,"symbol":"XSP","sections":[{"secType":"IND","symbol":"XSP"}]}]
        """;
        Assert.Empty(IBKRResponseParser.ParseOptionTradingClasses(json, 1));
    }

    [Theory]
    [InlineData("[]")]
    [InlineData("{}")]
    [InlineData("""[{"conid":"137851301","symbol":"XSP","sections":[{"secType":"OPT","symbol":"XSP"}]}]""")]
    public void Tolerates_odd_payloads(string json)
        => Assert.NotNull(IBKRResponseParser.ParseOptionTradingClasses(json, 137851301));

    [Fact]
    public void A_string_conid_still_matches()
    {
        // IBKR returns conid as a number or a string depending on endpoint.
        const string json = """
        [{"conid":"137851301","symbol":"XSP","sections":[{"secType":"OPT","symbol":"XSPW"}]}]
        """;
        Assert.Equal(new[] { "XSPW" },
            IBKRResponseParser.ParseOptionTradingClasses(json, 137851301));
    }
}
