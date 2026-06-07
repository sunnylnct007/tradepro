using Dapper;
using TradePro.Api.Providers.IG;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// Phase P2 — IGSnapshotHarvester unit + schema tests.
///
///   * ParseEntry: pure-function "SYMBOL:EPIC" parser, no DB.
///   * Schema-level: insert via the same column shape the harvester
///     writes, verify the generated `mid` + `spread_bps` columns
///     compute correctly and the partial-index "failure" lookup is
///     consistent.
///
/// Tests follow the existing PostgresFixture pattern used by
/// DataTrustTest + CatalystEnricherTest. Each test deletes its rows
/// up front to stay isolated.
/// </summary>
[Collection("postgres")]
public sealed class IGSnapshotHarvesterTest
{
    private readonly PostgresFixture _fx;

    public IGSnapshotHarvesterTest(PostgresFixture fx)
    {
        _fx = fx;
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("DELETE FROM ig_l1_snapshots WHERE symbol LIKE 'TEST_%';");
    }

    // ── Pure-function parser ─────────────────────────────────────

    [Theory]
    [InlineData("SPY:IX.D.SPTRD.IFE.IP",   "SPY",    "IX.D.SPTRD.IFE.IP")]
    [InlineData("EURUSD:CS.D.EURUSD.MINI.IP", "EURUSD", "CS.D.EURUSD.MINI.IP")]
    [InlineData(" QQQ : EPIC.X ",         "QQQ",    "EPIC.X")]
    public void ParseEntry_returns_symbol_and_epic(
        string input, string expectedSymbol, string expectedEpic)
    {
        var (sym, epic) = IGSnapshotHarvester.ParseEntry(input);
        Assert.Equal(expectedSymbol, sym);
        Assert.Equal(expectedEpic, epic);
    }

    [Theory]
    [InlineData("")]
    [InlineData("    ")]
    [InlineData("missingcolon")]
    [InlineData(":only-epic")]
    [InlineData("only-symbol:")]
    public void ParseEntry_returns_null_on_malformed(string input)
    {
        var (sym, epic) = IGSnapshotHarvester.ParseEntry(input);
        Assert.Null(sym);
        Assert.Null(epic);
    }

    // ── Schema-level: generated columns + filters ────────────────

    [Fact]
    public async Task Insert_computes_mid_and_spread_bps()
    {
        // 1.0500 / 1.0501 bid/ask → mid 1.05005, spread = 0.0001 / 1.05005 * 1e4 = ~0.95 bps
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO ig_l1_snapshots (symbol, epic, bid, ask, source)
            VALUES ('TEST_EURUSD', 'CS.D.EURUSD.MINI.IP', 1.05000, 1.05010, 'ig_markets');");

        var (mid, spreadBps) = await conn.QuerySingleAsync<(decimal mid, decimal spreadBps)>(@"
            SELECT mid, spread_bps FROM ig_l1_snapshots
            WHERE symbol = 'TEST_EURUSD';");
        Assert.Equal(1.05005m, mid);
        // ((1.05010 - 1.05000) / 1.05005) * 10000 ≈ 0.95
        Assert.InRange(spreadBps, 0.90m, 1.00m);
    }

    [Fact]
    public async Task Insert_with_null_bid_records_failure_for_market_closed()
    {
        // When the harvester gets a TRADEABLE-OFFLINE response with
        // no quote, it still inserts so the time series has no gap.
        // The partial "failures" index should pick this row up.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO ig_l1_snapshots (symbol, epic, bid, ask, market_status, source, error)
            VALUES ('TEST_SPY', 'IX.D.SPTRD.IFE.IP', NULL, NULL, 'OFFLINE',
                    'ig_markets', 'market_status:OFFLINE');");

        // mid + spread_bps should be NULL when bid/ask are.
        var (mid, spreadBps, error) = await conn.QuerySingleAsync<(
            decimal? mid, decimal? spreadBps, string error)>(@"
            SELECT mid, spread_bps, error FROM ig_l1_snapshots
            WHERE symbol = 'TEST_SPY';");
        Assert.Null(mid);
        Assert.Null(spreadBps);
        Assert.Equal("market_status:OFFLINE", error);

        // And the partial-failure index should be queryable.
        var failureCount = await conn.ExecuteScalarAsync<long>(@"
            SELECT COUNT(*) FROM ig_l1_snapshots
            WHERE symbol = 'TEST_SPY'
              AND (bid IS NULL OR ask IS NULL);");
        Assert.Equal(1, (int)failureCount);
    }

    [Fact]
    public async Task Time_window_query_returns_newest_first()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        // Three polls, 1 minute apart. Mid should differ.
        await conn.ExecuteAsync(@"
            INSERT INTO ig_l1_snapshots (symbol, epic, bid, ask, captured_at_utc, source)
            VALUES
                ('TEST_QQQ', 'EPIC.QQQ', 380.10, 380.12, NOW() - INTERVAL '2 minutes', 'ig_markets'),
                ('TEST_QQQ', 'EPIC.QQQ', 380.15, 380.17, NOW() - INTERVAL '1 minute',  'ig_markets'),
                ('TEST_QQQ', 'EPIC.QQQ', 380.20, 380.22, NOW(),                         'ig_markets');");

        var rows = (await conn.QueryAsync<(decimal bid, decimal ask)>(@"
            SELECT bid, ask FROM ig_l1_snapshots
            WHERE symbol = 'TEST_QQQ'
            ORDER BY captured_at_utc DESC;")).AsList();
        Assert.Equal(3, rows.Count);
        Assert.Equal(380.20m, rows[0].bid);
        Assert.Equal(380.15m, rows[1].bid);
        Assert.Equal(380.10m, rows[2].bid);
    }

    // ── Status holder defaults — Phase P2.1 ──────────────────────

    [Fact]
    public void Status_holder_defaults_to_disabled_never_ticked()
    {
        // What the cockpit shows BEFORE the harvester ticks for the
        // first time. The verdict computation lives in the endpoint
        // lambda (pure on these fields); we pin the holder defaults
        // here so a refactor that drops a field trips this test.
        var status = new IGHarvesterStatus();
        Assert.False(status.Enabled);
        Assert.Null(status.LastTickAtUtc);
        Assert.Null(status.NextTickEtaUtc);
        Assert.Equal(0, status.LastTickCaptured);
        Assert.Equal(0, status.LastTickFailed);
        Assert.Equal(0, status.ConfiguredEpicCount);
        Assert.Null(status.LastError);
        // StartedAtUtc is set at construction.
        Assert.True(status.StartedAtUtc > DateTime.UtcNow.AddMinutes(-1));
    }

    [Fact]
    public async Task Aggregate_query_excludes_null_quote_rows_from_spread_stats()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        // Two clean rows + one null-quote (market closed) row.
        await conn.ExecuteAsync(@"
            INSERT INTO ig_l1_snapshots (symbol, epic, bid, ask, source) VALUES
                ('TEST_AGG', 'EPIC.A', 100.00, 100.10, 'ig_markets'),
                ('TEST_AGG', 'EPIC.A', 101.00, 101.05, 'ig_markets'),
                ('TEST_AGG', 'EPIC.A', NULL,    NULL,    'ig_markets');");

        var (nPolls, nQuotes, avgSpread) = await conn.QuerySingleAsync<(
            int nPolls, int nQuotes, decimal? avgSpread)>(@"
            SELECT
                COUNT(*)::int                                                       AS nPolls,
                SUM(CASE WHEN bid IS NOT NULL AND ask IS NOT NULL THEN 1 ELSE 0 END)::int
                                                                                    AS nQuotes,
                AVG(spread_bps)                                                     AS avgSpread
            FROM ig_l1_snapshots
            WHERE symbol = 'TEST_AGG';");
        Assert.Equal(3, nPolls);
        Assert.Equal(2, nQuotes);
        // spread_bps is null on the no-quote row so the AVG ignores it.
        Assert.NotNull(avgSpread);
    }
}
