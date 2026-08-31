using System.Text.Json;
using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// IBKR abbreviates large numbers, and dropping them looks exactly like a dark feed.
///
/// THE DEFECT, 31 Aug 2026. Field 7638 (option open interest) arrives as the
/// string "9.21K" on liquid names. `decimal.TryParse` rejects that exactly as it
/// rejected "57.2%" for implied vol, so the value became null on precisely the
/// contracts where open interest is HIGHEST — the ones a liquidity gate most
/// wants to pass.
///
/// Measured with the market open, one chain call:
///
///     XOM  openInterest [548, 868, 857, 759, 619, 119]   all parsed
///     SPY  openInterest [null x6]         raw 7638 = "9.21K", "9.09K", "6.80K"
///     MRVL openInterest [null, 484, null, 675, null, 398]
///                                         raw 7638 = "1.99K", "1.34K"
///
/// Every null is a K-suffixed value; every number that survived was plain. The
/// feed was never patchy — the parse was. And the resulting nulls fed a
/// liquidity gate that had already been blamed on "IBKR doesn't serve OI".
///
/// FOURTH instance of this shape in IBKRResponseParser: an execution price
/// arriving as a string, an order id under a name nobody checked, a percent
/// suffix, and now a thousands suffix. The lesson each time is the same — read
/// the RAW payload before concluding a field is dark.
/// </summary>
public class IbkrAbbreviatedNumbersTest
{
    private static JsonElement Snap(string json)
        => JsonDocument.Parse(json).RootElement.Clone();

    // ParseSnapshotField exposes the same DecLoose path the chain uses.
    private static decimal? Field(string raw)
        => IBKRResponseParser.ParseSnapshotField(
               $$"""[{"conid": 1, "7638": {{raw}} }]""", "7638");

    [Theory]
    [InlineData("\"9.21K\"", 9210)]
    [InlineData("\"9.09K\"", 9090)]
    [InlineData("\"6.80K\"", 6800)]
    [InlineData("\"1.99K\"", 1990)]
    [InlineData("\"1.34K\"", 1340)]
    [InlineData("\"2K\"", 2000)]
    public void Thousands_suffix_is_expanded_not_dropped(string raw, decimal expected)
        => Assert.Equal(expected, Field(raw));

    [Theory]
    [InlineData("\"8.98M\"", 8_980_000)]      // field 7282 (average volume)
    [InlineData("\"1.5B\"", 1_500_000_000)]
    [InlineData("\"9.21k\"", 9210)]           // lower case happens too
    public void Millions_and_billions_are_expanded(string raw, decimal expected)
        => Assert.Equal(expected, Field(raw));

    [Theory]
    [InlineData("548", 548)]
    [InlineData("\"868\"", 868)]
    [InlineData("\"2,953\"", 2953)]           // thousands SEPARATOR, not a suffix
    [InlineData("\"57.2%\"", 57.2)]           // the previous instance must not regress
    public void Plain_values_are_unchanged(string raw, decimal expected)
        => Assert.Equal(expected, Field(raw));

    [Fact]
    public void A_bare_suffix_is_not_a_number()
    {
        // "K" alone must not become 1,000 — that would invent liquidity from a
        // malformed field, which is the dangerous direction for this gate.
        Assert.Null(Field("\"K\""));
    }

    [Fact]
    public void Genuinely_absent_still_returns_null()
    {
        Assert.Null(IBKRResponseParser.ParseSnapshotField(
            """[{"conid": 1}]""", "7638"));
    }
}
