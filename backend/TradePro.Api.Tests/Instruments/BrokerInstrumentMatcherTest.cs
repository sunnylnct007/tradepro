using TradePro.Api.Instruments;
using Xunit;

namespace TradePro.Api.Tests.Instruments;

/// <summary>
/// Coverage for the PURE, broker-agnostic instrument matcher. The catalog
/// here is SYNTHETIC (no live T212 data) but uses the broker's structural
/// ticker convention so the bare-root path is exercised exactly as it is
/// in production. The headline case is the silent-reject bug: universe
/// "META" must resolve to the broker's "FB_US_EQ" via NAME, because the
/// naive "META" + "_US_EQ" suffix would have routed to a non-existent
/// "META_US_EQ" and been rejected.
/// </summary>
public class BrokerInstrumentMatcherTest
{
    private static IReadOnlyList<BrokerInstrument> Catalog() => new[]
    {
        new BrokerInstrument("AAPL_US_EQ", "Apple Inc.", "US0378331005", "EQUITY", "USD"),
        new BrokerInstrument("FB_US_EQ",   "Meta Platforms", "US30303M1027", "EQUITY", "USD"),
        new BrokerInstrument("MSFT_US_EQ", "Microsoft Corporation", "US5949181045", "EQUITY", "USD"),
        // A name collision: two distinct "Acme" issuers — name match must refuse.
        new BrokerInstrument("ACME1_US_EQ", "Acme", null, "EQUITY", "USD"),
        new BrokerInstrument("ACME2_L_EQ",  "Acme", null, "EQUITY", "GBP"),
    };

    [Fact]
    public void Exact_match_via_bare_root()
    {
        var res = BrokerInstrumentMatcher.Resolve(
            Catalog(),
            new[] { ("AAPL", (string?)"Apple Inc.", (string?)null) });

        var m = Assert.Single(res);
        Assert.Equal("AAPL", m.SourceTicker);
        Assert.Equal("AAPL_US_EQ", m.BrokerTicker);
        Assert.Equal(MatchMethod.Exact, m.Method);
    }

    [Fact]
    public void Meta_resolves_to_FB_via_name_match()
    {
        // The bug: there is NO "META_US_EQ" in the catalog. Exact-root
        // fails; name "Meta Platforms, Inc." normalizes to the catalog's
        // "Meta Platforms" → unique hit → FB_US_EQ.
        var res = BrokerInstrumentMatcher.Resolve(
            Catalog(),
            new[] { ("META", (string?)"Meta Platforms, Inc.", (string?)null) });

        var m = Assert.Single(res);
        Assert.Equal("FB_US_EQ", m.BrokerTicker);
        Assert.Equal(MatchMethod.ByName, m.Method);
    }

    [Fact]
    public void Isin_is_preferred_over_ticker_and_name()
    {
        // Universe symbol is a deliberate root mismatch ("ZZZZ") whose
        // ISIN points at Apple — ISIN must win and route to AAPL_US_EQ.
        var res = BrokerInstrumentMatcher.Resolve(
            Catalog(),
            new[] { ("ZZZZ", (string?)"Totally Different Name", (string?)"US0378331005") });

        var m = Assert.Single(res);
        Assert.Equal("AAPL_US_EQ", m.BrokerTicker);
        Assert.Equal(MatchMethod.ByIsin, m.Method);
    }

    [Fact]
    public void Ambiguous_name_is_unresolved_never_guesses()
    {
        var res = BrokerInstrumentMatcher.Resolve(
            Catalog(),
            new[] { ("ACME", (string?)"Acme", (string?)null) });

        var m = Assert.Single(res);
        Assert.Null(m.BrokerTicker);
        Assert.Equal(MatchMethod.Unresolved, m.Method);
    }

    [Fact]
    public void Absent_symbol_is_unresolved()
    {
        var res = BrokerInstrumentMatcher.Resolve(
            Catalog(),
            new[] { ("NOTLISTED", (string?)"Nowhere Industries", (string?)null) });

        var m = Assert.Single(res);
        Assert.Null(m.BrokerTicker);
        Assert.Equal(MatchMethod.Unresolved, m.Method);
    }

    [Fact]
    public void Name_match_only_with_exactly_one_candidate()
    {
        // MSFT by exact root still wins even though we pass a name;
        // confirms priority order (exact before name).
        var res = BrokerInstrumentMatcher.Resolve(
            Catalog(),
            new[] { ("MSFT", (string?)"Microsoft Corp", (string?)null) });

        var m = Assert.Single(res);
        Assert.Equal("MSFT_US_EQ", m.BrokerTicker);
        Assert.Equal(MatchMethod.Exact, m.Method);
    }

    [Theory]
    [InlineData("Meta Platforms, Inc.", "META PLATFORMS")]
    [InlineData("Apple Inc.", "APPLE")]
    [InlineData("Microsoft Corporation", "MICROSOFT")]
    [InlineData("The Goldman Sachs Group, Inc.", "GOLDMAN SACHS")]
    [InlineData("Alphabet Inc. Class A", "ALPHABET")]
    [InlineData("Berkshire Hathaway Inc. Class B", "BERKSHIRE HATHAWAY")]
    [InlineData("Foo Holdings Group plc", "FOO")]
    [InlineData("  Spaced   Out  Co  ", "SPACED OUT")]
    [InlineData(null, "")]
    [InlineData("", "")]
    public void NormalizeName_cases(string? input, string expected)
        => Assert.Equal(expected, BrokerInstrumentMatcher.NormalizeName(input));

    [Fact]
    public void NormalizeName_does_not_reduce_to_empty_for_suffix_only_name()
    {
        // A name that is ONLY a corporate-form word should survive rather
        // than vanish (degenerate but must not throw / blank out).
        Assert.Equal("GROUP", BrokerInstrumentMatcher.NormalizeName("Group"));
    }
}
