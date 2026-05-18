using System.Security.Cryptography;
using System.Text;
using Dapper;
using Npgsql;

namespace TradePro.Api.Data;

/// <summary>
/// Applies SQL migration files from <c>db/migrations/</c> in numbered
/// order on app startup. Each migration runs inside a transaction;
/// the runner tracks applied migrations in the <c>schema_migrations</c>
/// table so re-running is a no-op.
///
/// Design choices worth keeping in mind:
/// <list type="bullet">
///   <item>One transaction per file. A failed migration rolls itself
///   back fully — there's no half-applied state to recover from.</item>
///   <item>Checksum recorded at apply time. A re-run that finds an
///   already-applied migration with a different body logs a warning
///   so you notice that someone edited a migration in place.</item>
///   <item>File order is lexicographic, hence the <c>NNN_*</c> naming
///   convention. Don't rename existing files — that breaks the
///   ordering invariant for anyone who already applied them.</item>
/// </list>
/// </summary>
public sealed class MigrationRunner
{
    private readonly string _connectionString;
    private readonly string _migrationsPath;
    private readonly ILogger<MigrationRunner> _log;

    public MigrationRunner(string connectionString, string migrationsPath, ILogger<MigrationRunner> log)
    {
        _connectionString = connectionString;
        _migrationsPath = migrationsPath;
        _log = log;
    }

    public async Task RunAsync(CancellationToken ct = default)
    {
        if (!Directory.Exists(_migrationsPath))
        {
            _log.LogWarning("Migrations directory not found: {path} — skipping", _migrationsPath);
            return;
        }

        // Bootstrap step: the schema_migrations table itself must
        // exist before we can check what's already applied. The
        // first migration file (001_schema_migrations.sql) creates
        // it; we just apply that one specially before consulting
        // the tracker.
        await using var bootstrap = new NpgsqlConnection(_connectionString);
        await bootstrap.OpenAsync(ct);
        var bootstrapFile = Path.Combine(_migrationsPath, "001_schema_migrations.sql");
        if (File.Exists(bootstrapFile))
        {
            var body = await File.ReadAllTextAsync(bootstrapFile, ct);
            await bootstrap.ExecuteAsync(body);
        }

        // Now the regular loop.
        var applied = (await bootstrap.QueryAsync<(string Name, string Checksum)>(
            "SELECT name, checksum FROM schema_migrations"))
            .ToDictionary(x => x.Name, x => x.Checksum);

        var files = Directory.GetFiles(_migrationsPath, "*.sql")
            .OrderBy(f => f, StringComparer.Ordinal)
            .ToArray();

        foreach (var file in files)
        {
            ct.ThrowIfCancellationRequested();
            var name = Path.GetFileNameWithoutExtension(file);
            var body = await File.ReadAllTextAsync(file, ct);
            var checksum = Checksum(body);

            if (applied.TryGetValue(name, out var existing))
            {
                if (!string.Equals(existing, checksum, StringComparison.Ordinal))
                {
                    _log.LogWarning(
                        "Migration {name} checksum drift detected (recorded={existing}, current={current}). The file was edited after being applied — investigate before continuing.",
                        name, existing, checksum);
                }
                continue;
            }

            _log.LogInformation("Applying migration {name}", name);
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync(ct);
            await using var tx = await conn.BeginTransactionAsync(ct);
            try
            {
                await conn.ExecuteAsync(body, transaction: tx);
                await conn.ExecuteAsync(
                    "INSERT INTO schema_migrations (name, checksum) VALUES (@name, @checksum) ON CONFLICT (name) DO NOTHING",
                    new { name, checksum }, transaction: tx);
                await tx.CommitAsync(ct);
                _log.LogInformation("Migration {name} applied", name);
            }
            catch (Exception ex)
            {
                await tx.RollbackAsync(ct);
                _log.LogError(ex, "Migration {name} failed; rolled back", name);
                throw;
            }
        }
    }

    private static string Checksum(string body)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(body));
        var sb = new StringBuilder(bytes.Length * 2);
        foreach (var b in bytes) sb.Append(b.ToString("x2"));
        return sb.ToString();
    }
}
