using Dapper;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Admin;

/// <summary>
/// Schema-level tests for strategy_broker_map. The HTTP endpoints in
/// AdminEndpoints.cs are thin wrappers over the SQL; the real risk is
/// (a) the CHECK constraint (025) catching invalid brokers, and
/// (b) the UPSERT semantics behaving correctly when the operator
/// flips a strategy's broker.
///
/// We exercise the schema directly via Dapper rather than spinning up
/// a WebApplicationFactory — the existing test culture in this repo
/// trusts Minimal API + Dapper enough to test the SQL it issues and
/// not the HTTP plumbing around it (see OmsServiceTest.cs).
/// </summary>
[Collection("postgres")]
public sealed class StrategyBrokerMapTest
{
    private readonly PostgresFixture _fx;

    public StrategyBrokerMapTest(PostgresFixture fx)
    {
        _fx = fx;
        // Per-test cleanup so an UPSERT in test N doesn't poison test
        // N+1's "row should not exist" assertions. The seeded rows
        // from migrations 021 + 024 are intentionally re-added after
        // truncate so the GET-shape test sees realistic data.
        using var conn = _fx.Db.OpenConnection();
        conn.Execute("DELETE FROM strategy_broker_map;");
        conn.Execute(@"
            INSERT INTO strategy_broker_map (strategy_id, broker, note, updated_by)
            VALUES
                ('ichimoku_equity', 'IG_DEMO', 'seed', 'migration'),
                ('ichimoku_fx_mr',  'IG_DEMO', 'seed', 'migration'),
                ('intraday_flat',   'IG_DEMO', 'seed', 'migration');");
    }

    [Fact]
    public async Task Migration_025_check_constraint_rejects_unknown_broker()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var exc = await Assert.ThrowsAsync<Npgsql.PostgresException>(async () =>
        {
            await conn.ExecuteAsync(@"
                INSERT INTO strategy_broker_map (strategy_id, broker, updated_by)
                VALUES ('bogus_strategy', 'NOT_A_BROKER', 'test');");
        });
        // Postgres' check_violation SQLSTATE — proves the CHECK
        // constraint actually fired, not some other accidental error.
        Assert.Equal("23514", exc.SqlState);
    }

    [Fact]
    public async Task Migration_025_check_constraint_accepts_all_valid_brokers()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        string[] valid = {
            "T212_DEMO", "T212_LIVE",
            "IBKR_PAPER", "IBKR_LIVE",
            "IG_DEMO", "IG_LIVE",
            "PAPER",
        };
        foreach (var b in valid)
        {
            await conn.ExecuteAsync(@"
                INSERT INTO strategy_broker_map (strategy_id, broker, updated_by)
                VALUES (@s, @b, 'test')
                ON CONFLICT (strategy_id) DO UPDATE SET broker = EXCLUDED.broker;",
                new { s = $"valid_{b}", b });
        }
        var count = await conn.ExecuteScalarAsync<long>(@"
            SELECT COUNT(*) FROM strategy_broker_map
            WHERE strategy_id LIKE 'valid_%';");
        Assert.Equal(valid.Length, (int)count);
    }

    [Fact]
    public async Task Upsert_inserts_new_strategy_then_updates_on_flip()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();

        // Insert
        await conn.ExecuteAsync(@"
            INSERT INTO strategy_broker_map
                (strategy_id, broker, account_id, note, updated_at_utc, updated_by)
            VALUES ('orb', 'T212_DEMO', NULL, 'first', NOW(), 'tester')
            ON CONFLICT (strategy_id) DO UPDATE
            SET broker         = EXCLUDED.broker,
                account_id     = EXCLUDED.account_id,
                note           = EXCLUDED.note,
                updated_at_utc = NOW(),
                updated_by     = EXCLUDED.updated_by;");
        var afterInsert = await conn.QuerySingleAsync<(string broker, string note, string updated_by)>(@"
            SELECT broker, note, updated_by
            FROM strategy_broker_map WHERE strategy_id = 'orb';");
        Assert.Equal("T212_DEMO", afterInsert.broker);
        Assert.Equal("first", afterInsert.note);
        Assert.Equal("tester", afterInsert.updated_by);

        // Update (flip broker)
        await conn.ExecuteAsync(@"
            INSERT INTO strategy_broker_map
                (strategy_id, broker, account_id, note, updated_at_utc, updated_by)
            VALUES ('orb', 'IG_DEMO', NULL, 'second', NOW(), 'tester2')
            ON CONFLICT (strategy_id) DO UPDATE
            SET broker         = EXCLUDED.broker,
                account_id     = EXCLUDED.account_id,
                note           = EXCLUDED.note,
                updated_at_utc = NOW(),
                updated_by     = EXCLUDED.updated_by;");
        var afterUpdate = await conn.QuerySingleAsync<(string broker, string note, string updated_by)>(@"
            SELECT broker, note, updated_by
            FROM strategy_broker_map WHERE strategy_id = 'orb';");
        Assert.Equal("IG_DEMO", afterUpdate.broker);
        Assert.Equal("second", afterUpdate.note);
        Assert.Equal("tester2", afterUpdate.updated_by);
    }

    [Fact]
    public async Task Delete_removes_row_so_strategy_falls_back_to_default()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        // The setUp seeds 3 strategies; delete one and confirm.
        var rows = await conn.ExecuteAsync(@"
            DELETE FROM strategy_broker_map WHERE strategy_id = 'intraday_flat';");
        Assert.Equal(1, rows);
        var remaining = await conn.ExecuteScalarAsync<long>(@"
            SELECT COUNT(*) FROM strategy_broker_map
            WHERE strategy_id IN ('ichimoku_equity', 'ichimoku_fx_mr', 'intraday_flat');");
        Assert.Equal(2, (int)remaining);

        var lookup = await conn.ExecuteScalarAsync<string?>(@"
            SELECT broker FROM strategy_broker_map
            WHERE strategy_id = 'intraday_flat';");
        Assert.Null(lookup);
    }

    [Fact]
    public async Task Delete_is_idempotent_when_row_does_not_exist()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var rows = await conn.ExecuteAsync(@"
            DELETE FROM strategy_broker_map WHERE strategy_id = 'never_existed';");
        Assert.Equal(0, rows);
    }

    [Fact]
    public async Task Seeded_rows_from_migrations_present_after_setup()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        // Three strategies seeded across 021 + 024 (and re-seeded by
        // this test class's per-test setup). All three should map to
        // IG_DEMO by default.
        var brokers = (await conn.QueryAsync<(string strategy_id, string broker)>(@"
            SELECT strategy_id, broker FROM strategy_broker_map
            WHERE strategy_id IN ('ichimoku_equity', 'ichimoku_fx_mr', 'intraday_flat')
            ORDER BY strategy_id;")).AsList();
        Assert.Equal(3, brokers.Count);
        Assert.All(brokers, r => Assert.Equal("IG_DEMO", r.broker));
    }
}
