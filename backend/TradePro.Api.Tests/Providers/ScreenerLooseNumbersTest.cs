using System.Text.Json;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The screener scored on zeroes it never received, and emailed the result.
///
/// THE DEFECT, 1 Sep 2026. The owner: "the mail i receievd just now saying 0
/// wheel candidate" — while the desk board showed 21 eligible from the same
/// account at the same moment.
///
/// Probed SPY with the nine fields ScreenerEndpoints requests. TWO came back:
///
///     requested: 31, 7293, 7294, 7282, 7283, 7631, 7286, 87, 7718
///     returned : 31 = "764.10",  7283 = "11.950%"
///
/// Both failure modes then applied:
///   * the seven MISSING fields became 0.0 via `Num(..., def = 0.0)`;
///   * the one field IBKR DID serve, "11.950%", failed bare double.TryParse on
///     the percent suffix and ALSO became 0.0.
///
/// So `ivp=0.0 hv=0.0 div=0.0`, the wheel scored 4/14 against minimum 5, every
/// name failed, and the owner got a confident "0 candidates" email daily.
///
/// Fifth instance of this parse shape in the codebase — an execution price as a
/// string, an order id under an unchecked name, IV as "57.2%", open interest as
/// "9.21K", now this.
/// </summary>
public class ScreenerLooseNumbersTest
{
    private static JsonElement V(string json)
        => JsonDocument.Parse(json).RootElement.Clone();

    private static double? Parse(string raw)
    {
        var m = typeof(TradePro.Api.Endpoints.ScreenerEndpoints)
            .GetMethod("ParseLoose", System.Reflection.BindingFlags.NonPublic
                                     | System.Reflection.BindingFlags.Static)!;
        return (double?)m.Invoke(null, new object[] { V(raw) });
    }

    [Theory]
    [InlineData("\"11.950%\"", 11.950)]   // the exact SPY value that read as 0
    [InlineData("\"57.2%\"", 57.2)]
    [InlineData("\"1,227.50\"", 1227.50)]
    [InlineData("\"C764.10\"", 764.10)]   // close-prefixed
    [InlineData("764.10", 764.10)]
    [InlineData("\"8.98M\"", 8_980_000)]
    [InlineData("\"9.21K\"", 9210)]
    public void Display_formatted_numbers_parse(string raw, double expected)
        => Assert.Equal(expected, Parse(raw)!.Value, 3);

    [Theory]
    [InlineData("\"\"")]
    [InlineData("\"  \"")]
    [InlineData("\"N/A\"")]
    [InlineData("\"K\"")]                 // a bare suffix must not become 1,000
    [InlineData("null")]
    public void Unusable_values_return_null_not_zero(string raw)
        => Assert.Null(Parse(raw));

    [Fact]
    public void Zero_is_preserved_as_a_real_zero()
    {
        // A genuine 0 must survive — null means "not served", 0 means "served
        // as none". Collapsing them is the bug one level up.
        Assert.Equal(0.0, Parse("0")!.Value);
        Assert.Equal(0.0, Parse("\"0%\"")!.Value);
    }
}
