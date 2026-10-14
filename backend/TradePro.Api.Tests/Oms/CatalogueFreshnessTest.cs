using Dapper;
using System.Text.Json;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Oms;

/// <summary>Reproduces the 14 Oct regression before fixing it.
///
/// #294 added a freshness read on paper_strategies.updated_at so a stale
/// catalogue could not be trusted. After it deployed the health panel went
/// back to "registry unavailable" on a catalogue that had been pushed hours
/// earlier — i.e. the guard threw and fell into its own catch, disabling the
/// feature it was meant to protect.
/// </summary>
[Collection("postgres")]
public sealed class CatalogueFreshnessTest
{
    private readonly PostgresFixture _fx;
    public CatalogueFreshnessTest(PostgresFixture fx) => _fx = fx;

    [Fact]
    public async Task A_freshly_pushed_catalogue_reads_as_fresh()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO paper_strategies (id, payload, updated_at)
            VALUES ('singleton', '{""strategies"":[]}'::jsonb, NOW())
            ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW();");

        // THE EXACT QUERY THE ENDPOINT RUNS. Age is computed in Postgres: every
        // client-side option has a way to be quietly wrong, and #294 found one
        // the hard way (see below).
        var ageDays = await conn.ExecuteScalarAsync<double?>(
            @"SELECT EXTRACT(EPOCH FROM (NOW() - updated_at)) / 86400.0
                FROM paper_strategies WHERE id = 'singleton'");

        Assert.True(ageDays.HasValue, "freshness read returned null for a row just written");
        Assert.True(ageDays.Value >= 0, $"negative age ({ageDays}) — a timezone mismatch");
        Assert.True(ageDays.Value < 1,
            $"a catalogue written NOW reads as {ageDays:F1} days old — the guard would " +
            "call a fresh catalogue stale and disable retired-vs-stale classification");
    }

    /// <summary>The cast #294 actually shipped, pinned as a NEGATIVE test.
    ///
    /// Npgsql hands TIMESTAMPTZ back as a DateTime, so asking Dapper for a
    /// DateTimeOffset throws InvalidCastException. In the endpoint that throw
    /// landed in a bare catch, registryKnown went false, and retired-vs-stale
    /// was silently disabled for a day on a catalogue pushed hours earlier.
    ///
    /// Kept so nobody "tidies" the EXTRACT(EPOCH ...) back into a typed read on
    /// the reasonable-sounding grounds that DateTimeOffset is the safer type.
    /// It is not; it does not work at all.
    /// </summary>
    [Fact]
    public async Task Reading_it_as_DateTimeOffset_throws_which_is_why_we_do_not()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO paper_strategies (id, payload, updated_at)
            VALUES ('singleton', '{""strategies"":[]}'::jsonb, NOW())
            ON CONFLICT (id) DO UPDATE SET updated_at = NOW();");

        await Assert.ThrowsAsync<InvalidCastException>(async () =>
            await conn.ExecuteScalarAsync<DateTimeOffset?>(
                "SELECT updated_at FROM paper_strategies WHERE id = 'singleton'"));
    }
}
