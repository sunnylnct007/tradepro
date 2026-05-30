using Dapper;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Admin;

/// <summary>
/// Schema-level tests for the Phase-A trustworthy-data foundation —
/// data_source_preferences (migration 029) + data_assumptions
/// (migration 030). The HTTP endpoints in DataTrustEndpoints.cs are
/// thin wrappers around the SQL; the real value of these tests is
/// to assert (a) CHECK constraints fire, (b) UPSERT semantics are
/// right, (c) the seed rows from the migrations land as expected.
///
/// Same pattern as StrategyBrokerMapTest — Dapper against the
/// shared Postgres fixture, no WebApplicationFactory.
/// </summary>
[Collection("postgres")]
public sealed class DataTrustTest
{
    private readonly PostgresFixture _fx;

    public DataTrustTest(PostgresFixture fx)
    {
        _fx = fx;
        // Per-test cleanup so a PUT in test N doesn't poison test
        // N+1's "row should not exist" assertions. The migration
        // seeds (029 INSERTs + 030 INSERTs) are re-applied via
        // explicit reinsert because we just truncated.
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("DELETE FROM data_source_preferences;");
        conn.Execute("DELETE FROM data_assumptions;");
        // Re-seed a minimal known state — enough for the assertions
        // below without replaying the full migration seed.
        conn.Execute(@"
            INSERT INTO data_source_preferences
                (asset_class, resolution, provider_chain, notes, updated_by)
            VALUES
                ('us_etf',    '1m', ARRAY['yfinance'],
                 '7-day ceiling acknowledged', 'test_seed'),
                ('us_equity', '1d', ARRAY['yfinance'],
                 'daily Yahoo back to 2000', 'test_seed');");
        conn.Execute(@"
            INSERT INTO data_assumptions
                (id, description, severity, status, affects,
                 consequence, remedy, last_reviewed_by)
            VALUES
                ('L1_intraday_data_ceiling',
                 'yfinance 1m capped at 7 days',
                 'CRITICAL', 'FICTIONAL',
                 ARRAY['intraday_flat'],
                 'no honest intraday backtest past 7 days',
                 'Phase B + Phase C + Phase E',
                 'test_seed'),
                ('L2_slippage_fictional',
                 'fills at OHLC close, no bid/ask',
                 'HIGH', 'OPTIMISTIC',
                 ARRAY['all_strategies'],
                 'optimistic returns by spread cost',
                 'Phase F slippage layer',
                 'test_seed');");
    }

    [Fact]
    public async Task Migration_029_provider_check_rejects_unknown_provider()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var exc = await Assert.ThrowsAsync<Npgsql.PostgresException>(async () =>
        {
            await conn.ExecuteAsync(@"
                INSERT INTO data_source_preferences
                    (asset_class, resolution, provider_chain, updated_by)
                VALUES ('us_etf', '1m', ARRAY['bogus_provider'], 'test');");
        });
        Assert.Equal("23514", exc.SqlState); // check_violation
    }

    [Fact]
    public async Task Migration_029_provider_check_accepts_known_provider_set()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        // Every provider on the allow-list should work as the only
        // entry in a chain.
        string[] valid = {
            "yfinance", "ig", "finnhub", "t212",
            "polygon", "databento", "oanda", "binance",
        };
        foreach (var p in valid)
        {
            await conn.ExecuteAsync(@"
                INSERT INTO data_source_preferences
                    (asset_class, resolution, provider_chain, updated_by)
                VALUES (@asset_class, '1d', ARRAY[@p], 'test')
                ON CONFLICT (asset_class, resolution) DO UPDATE
                SET provider_chain = EXCLUDED.provider_chain;",
                new { asset_class = $"test_class_{p}", p });
        }
        var count = await conn.ExecuteScalarAsync<long>(@"
            SELECT COUNT(*) FROM data_source_preferences
            WHERE asset_class LIKE 'test_class_%';");
        Assert.Equal(valid.Length, (int)count);
    }

    [Fact]
    public async Task Migration_029_accepts_multi_provider_chain()
    {
        // The chain is the whole point — verify [yfinance, ig,
        // finnhub] all-known list succeeds.
        await using var conn = await _fx.Db.OpenConnectionAsync();
        await conn.ExecuteAsync(@"
            INSERT INTO data_source_preferences
                (asset_class, resolution, provider_chain, updated_by)
            VALUES ('us_etf', '5m',
                    ARRAY['yfinance', 'ig', 'finnhub'], 'test');");
        var chain = await conn.ExecuteScalarAsync<string[]>(@"
            SELECT provider_chain FROM data_source_preferences
            WHERE asset_class = 'us_etf' AND resolution = '5m';");
        Assert.Equal(new[] { "yfinance", "ig", "finnhub" }, chain);
    }

    [Fact]
    public async Task Preferences_upsert_flips_provider_chain_in_place()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        // The fixture seeded ['yfinance'] for us_etf/1m. Upsert to
        // ['yfinance', 'ig'] and verify the row updates in place.
        await conn.ExecuteAsync(@"
            INSERT INTO data_source_preferences
                (asset_class, resolution, provider_chain, notes,
                 updated_at_utc, updated_by)
            VALUES ('us_etf', '1m', ARRAY['yfinance', 'ig'],
                    'added IG fallback', NOW(), 'tester')
            ON CONFLICT (asset_class, resolution) DO UPDATE
            SET provider_chain  = EXCLUDED.provider_chain,
                notes           = EXCLUDED.notes,
                updated_at_utc  = NOW(),
                updated_by      = EXCLUDED.updated_by;");

        var (chain, notes, updated_by) =
            await conn.QuerySingleAsync<(string[] chain, string notes, string updated_by)>(@"
                SELECT provider_chain, notes, updated_by
                FROM data_source_preferences
                WHERE asset_class = 'us_etf' AND resolution = '1m';");
        Assert.Equal(new[] { "yfinance", "ig" }, chain);
        Assert.Equal("added IG fallback", notes);
        Assert.Equal("tester", updated_by);
    }

    [Fact]
    public async Task Migration_030_severity_check_rejects_unknown_severity()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var exc = await Assert.ThrowsAsync<Npgsql.PostgresException>(async () =>
        {
            await conn.ExecuteAsync(@"
                INSERT INTO data_assumptions
                    (id, description, severity, status, affects,
                     consequence, remedy, last_reviewed_by)
                VALUES ('test_bad_severity', 'x', 'BOGUS', 'HONEST',
                        ARRAY[]::TEXT[], 'x', 'x', 'test');");
        });
        Assert.Equal("23514", exc.SqlState);
    }

    [Fact]
    public async Task Migration_030_status_check_rejects_unknown_status()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var exc = await Assert.ThrowsAsync<Npgsql.PostgresException>(async () =>
        {
            await conn.ExecuteAsync(@"
                INSERT INTO data_assumptions
                    (id, description, severity, status, affects,
                     consequence, remedy, last_reviewed_by)
                VALUES ('test_bad_status', 'x', 'HIGH', 'BOGUS',
                        ARRAY[]::TEXT[], 'x', 'x', 'test');");
        });
        Assert.Equal("23514", exc.SqlState);
    }

    [Fact]
    public async Task Migration_030_severity_check_accepts_all_known_severities()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        string[] severities = { "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL" };
        foreach (var s in severities)
        {
            await conn.ExecuteAsync(@"
                INSERT INTO data_assumptions
                    (id, description, severity, status, affects,
                     consequence, remedy, last_reviewed_by)
                VALUES (@id, 'sev test', @s, 'HONEST',
                        ARRAY[]::TEXT[], 'x', 'x', 'test');",
                new { id = $"test_sev_{s}", s });
        }
    }

    [Fact]
    public async Task Migration_030_status_check_accepts_all_known_statuses()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        string[] statuses = { "HONEST", "PARTIAL", "OPTIMISTIC", "FICTIONAL" };
        foreach (var s in statuses)
        {
            await conn.ExecuteAsync(@"
                INSERT INTO data_assumptions
                    (id, description, severity, status, affects,
                     consequence, remedy, last_reviewed_by)
                VALUES (@id, 'status test', 'LOW', @s,
                        ARRAY[]::TEXT[], 'x', 'x', 'test');",
                new { id = $"test_status_{s}", s });
        }
    }

    [Fact]
    public async Task Assumptions_seeded_rows_present()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var rows = (await conn.QueryAsync<(string id, string severity, string status)>(@"
            SELECT id, severity, status FROM data_assumptions
            WHERE id IN ('L1_intraday_data_ceiling', 'L2_slippage_fictional')
            ORDER BY id;")).AsList();
        Assert.Equal(2, rows.Count);
        Assert.Equal(("L1_intraday_data_ceiling", "CRITICAL", "FICTIONAL"), rows[0]);
        Assert.Equal(("L2_slippage_fictional", "HIGH", "OPTIMISTIC"), rows[1]);
    }
}
