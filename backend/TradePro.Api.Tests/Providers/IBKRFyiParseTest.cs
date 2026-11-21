using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>IBKR FYI parsing, on the shape IBKR documents for
/// GET /fyi/notifications (Web API v1.0).</summary>
public class IBKRFyiParseTest
{
    private const string Doc = """
        [{"R":"0","D":"1710847062.0","MS":"FYI: Changes in Analyst Ratings",
          "MD":"<html>Analysts changed ratings.<br /> - RF@NYSE: Buy: 4(+1)</html>",
          "ID":"2024031947509444","HT":0,"FC":"PF"}]
        """;

    [Fact]
    public void Parses_the_documented_shape_and_strips_html()
    {
        var f = IBKRClient.ParseFyis(Doc).Single();
        Assert.Equal("2024031947509444", f.Id);
        Assert.Equal("FYI: Changes in Analyst Ratings", f.Title);
        Assert.Equal("PF", f.Code);
        Assert.False(f.Read);
        Assert.Equal(DateTimeOffset.FromUnixTimeSeconds(1710847062), f.At);
        Assert.DoesNotContain("<", f.Text);
        Assert.Contains("RF@NYSE: Buy: 4(+1)", f.Text);
    }

    [Fact]
    public void An_empty_or_non_array_body_is_an_empty_list()
    {
        Assert.Empty(IBKRClient.ParseFyis(""));
        Assert.Empty(IBKRClient.ParseFyis("{\"error\":\"x\"}"));
    }
}
