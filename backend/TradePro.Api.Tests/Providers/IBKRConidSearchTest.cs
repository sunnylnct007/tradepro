using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// Venue preference for /iserver/secdef/search parsing. IBKR's "best-first"
/// ranking is not ours: measured 22 Aug 2026, 16 liquid US names resolved to
/// foreign listings (BA → BAE Systems/LSE, SN → Smith &amp; Nephew/LSE, plus
/// C, T, GLD, MCD, …) whose chart data the account has no API entitlement
/// for, so every bar fetch died with "Chart data unavailable" while
/// unambiguous tickers worked. The parser must prefer the first US-venue
/// listing and fall back to IBKR's first hit only when no US listing exists.
/// </summary>
public class IBKRConidSearchTest
{
    [Fact]
    public void Prefers_the_US_listing_over_a_foreign_first_hit()
    {
        // The BA shape: LSE (BAE Systems) ranked first, NYSE (Boeing) second.
        const string json = """
            [
              {"conid": 11673684, "symbol": "BA", "description": "LSE",  "companyName": "BAE SYSTEMS PLC"},
              {"conid": 4762,     "symbol": "BA", "description": "NYSE", "companyName": "BOEING CO"}
            ]
            """;
        Assert.Equal(4762, IBKRResponseParser.ParseConidSearch(json));
    }

    [Fact]
    public void Falls_back_to_first_hit_when_no_US_listing_exists()
    {
        const string json = """
            [
              {"conid": 111, "symbol": "XX", "description": "LSE"},
              {"conid": 222, "symbol": "XX", "description": "IBIS"}
            ]
            """;
        Assert.Equal(111, IBKRResponseParser.ParseConidSearch(json));
    }

    [Fact]
    public void String_conids_and_missing_description_still_parse()
    {
        const string json = """
            [
              {"conid": "333", "symbol": "YY"}
            ]
            """;
        Assert.Equal(333, IBKRResponseParser.ParseConidSearch(json));
    }

    [Fact]
    public void US_hit_first_returns_immediately()
    {
        const string json = """
            [
              {"conid": 555, "symbol": "BAC", "description": "NYSE"},
              {"conid": 666, "symbol": "BAC", "description": "MEXI"}
            ]
            """;
        Assert.Equal(555, IBKRResponseParser.ParseConidSearch(json));
    }

    [Fact]
    public void Non_array_or_empty_payload_returns_null()
    {
        Assert.Null(IBKRResponseParser.ParseConidSearch("{\"error\":\"nope\"}"));
        Assert.Null(IBKRResponseParser.ParseConidSearch("[]"));
    }
}
