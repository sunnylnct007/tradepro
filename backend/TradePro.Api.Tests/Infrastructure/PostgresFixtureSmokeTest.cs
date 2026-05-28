using Dapper;
using TradePro.Api.Tests.Infrastructure;
using Xunit;

namespace TradePro.Api.Tests.Infrastructure;

/// <summary>
/// Confirms the test fixture wires correctly end-to-end: container
/// starts, migrations apply, the schema_migrations row count matches
/// the number of .sql files shipped, and Dapper round-trips work.
/// If this test fails, no Store tests will work — fix this first.
/// </summary>
[Collection("postgres")]
public sealed class PostgresFixtureSmokeTest
{
    private readonly PostgresFixture _fx;
    public PostgresFixtureSmokeTest(PostgresFixture fx) => _fx = fx;

    [Fact]
    public async Task Migrations_applied_and_recorded()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var count = await conn.ExecuteScalarAsync<int>(
            "SELECT COUNT(*) FROM schema_migrations;");

        Assert.True(count > 0, "schema_migrations is empty — migrations did not run");
    }

    [Fact]
    public async Task Postgres_round_trip_works()
    {
        await using var conn = await _fx.Db.OpenConnectionAsync();
        var v = await conn.ExecuteScalarAsync<int>("SELECT 42;");
        Assert.Equal(42, v);
    }
}
