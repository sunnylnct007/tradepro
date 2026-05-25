using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using TradePro.Api.Data;
using Testcontainers.PostgreSql;
using Xunit;

namespace TradePro.Api.Tests.Infrastructure;

/// <summary>
/// xUnit IAsyncLifetime fixture that spins up an ephemeral Postgres
/// container per test class, runs the API's migration set against it,
/// and exposes a ready-to-use NpgsqlDataSource. Reuses the prod
/// MigrationRunner so a test failure on a fresh schema is a true
/// migration bug, not a duplicated test-only init script.
/// </summary>
public sealed class PostgresFixture : IAsyncLifetime
{
    private readonly PostgreSqlContainer _container = new PostgreSqlBuilder()
        .WithImage("postgres:16-alpine")
        .WithDatabase("tradepro_test")
        .WithUsername("tradepro")
        .WithPassword("tradepro")
        .Build();

    public NpgsqlDataSource Db { get; private set; } = null!;
    public string ConnectionString => _container.GetConnectionString();

    public async Task InitializeAsync()
    {
        await _container.StartAsync();
        Db = NpgsqlDataSource.Create(ConnectionString);

        // Apply migrations the same way the API does on startup. The
        // SQL files were CopyToOutputDirectory'd into bin/.../db/migrations
        // via the csproj <None Include> wildcard.
        var migrationsDir = Path.Combine(AppContext.BaseDirectory, "db", "migrations");
        var runner = new MigrationRunner(
            ConnectionString,
            migrationsDir,
            NullLogger<MigrationRunner>.Instance);
        await runner.RunAsync();
    }

    public async Task DisposeAsync()
    {
        await Db.DisposeAsync();
        await _container.DisposeAsync();
    }
}

/// <summary>
/// Shared collection name so multiple test classes can share one
/// container instance (faster) when they don't mutate cross-table
/// state. Use [Collection("postgres")] on the test class.
/// </summary>
[CollectionDefinition("postgres")]
public sealed class PostgresCollection : ICollectionFixture<PostgresFixture> { }
