using System.Text.Json;
using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The predicate that decides whether the desk shows a price or a warning.
///
/// IBKR answers a field it cannot serve with the literal string "N/A", and a
/// snapshot full of them arrives as an ordinary HTTP 200. That shape is why the
/// health probe reported "ok — auth + live snapshot" for an entire trading day
/// on 18 Aug 2026 while every symbol was dark and the wheel board ran on
/// carried prices. `"N/A" is not None` was True.
///
/// It also has to refuse values IBKR has explicitly marked as NOT live: field 31
/// comes back "C"-prefixed when the number is the previous CLOSE, and "H"-prefixed
/// when the instrument is halted. Both are real numbers, and rendering either as
/// a live print is the exact failure the live-quote endpoint exists to prevent.
/// </summary>
public class IbkrQuoteFieldsTest
{
    private static JsonElement Snap(string json)
        => JsonDocument.Parse(json).RootElement.Clone();

    [Theory]
    [InlineData("227.50", 227.50)]
    [InlineData("\"227.50\"", 227.50)]
    [InlineData("\"1,227.50\"", 1227.50)]   // thousands separator
    [InlineData("\"D227.50\"", 227.50)]     // delayed-feed prefix is still a price
    public void RealOrNull_returns_the_number_when_ibkr_gives_one(string value, decimal expected)
    {
        var got = IbkrQuoteFields.RealOrNull(Snap($$"""{"31": {{value}} }"""), "31");
        Assert.Equal(expected, got);
    }

    [Theory]
    [InlineData("\"N/A\"")]
    [InlineData("\"n/a\"")]
    [InlineData("\"NA\"")]
    [InlineData("\"\"")]
    [InlineData("\"  \"")]
    [InlineData("\"-\"")]
    [InlineData("\"None\"")]
    public void RealOrNull_refuses_ibkrs_way_of_saying_it_has_no_value(string value)
    {
        var got = IbkrQuoteFields.RealOrNull(Snap($$"""{"31": {{value}} }"""), "31");
        Assert.Null(got);
    }

    [Theory]
    [InlineData("\"C227.50\"")]   // previous CLOSE, not a live last
    [InlineData("\"c227.50\"")]
    [InlineData("\"H227.50\"")]   // halted
    public void RealOrNull_refuses_a_number_ibkr_marked_as_not_live(string value)
    {
        var got = IbkrQuoteFields.RealOrNull(Snap($$"""{"31": {{value}} }"""), "31");
        Assert.Null(got);
    }

    [Fact]
    public void RealOrNull_returns_null_for_a_missing_field()
    {
        Assert.Null(IbkrQuoteFields.RealOrNull(Snap("""{"84": "1.00"}"""), "31"));
    }

    [Fact]
    public void RealOrNull_returns_null_when_the_snapshot_is_not_an_object()
    {
        Assert.Null(IbkrQuoteFields.RealOrNull(Snap("[]"), "31"));
    }

    [Fact]
    public void A_snapshot_of_all_NA_reads_as_dark_not_as_a_quote()
    {
        // The exact body that certified an all-day outage as healthy: HTTP 200,
        // every field present, not one of them a price.
        var snap = Snap("""{"31":"N/A","84":"N/A","86":"N/A","7283":"N/A"}""");
        var last = IbkrQuoteFields.RealOrNull(snap, "31");
        var bid = IbkrQuoteFields.RealOrNull(snap, "84");
        var ask = IbkrQuoteFields.RealOrNull(snap, "86");

        Assert.Null(last);
        Assert.Null(bid);
        Assert.Null(ask);
        Assert.False(last is not null || bid is not null || ask is not null,
            "a snapshot with no real field must not read as live");
    }

    [Fact]
    public void A_bid_alone_still_counts_as_live()
    {
        // Pre-open and thin names legitimately have no last print. Refusing the
        // whole quote there would be its own false alarm.
        var snap = Snap("""{"31":"N/A","84":"226.90","86":"N/A"}""");
        var live = IbkrQuoteFields.RealOrNull(snap, "31") is not null
                   || IbkrQuoteFields.RealOrNull(snap, "84") is not null
                   || IbkrQuoteFields.RealOrNull(snap, "86") is not null;
        Assert.True(live);
    }
}
