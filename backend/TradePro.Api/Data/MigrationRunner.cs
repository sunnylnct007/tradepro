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
    /// <summary>
    /// Per-migration command timeout, in seconds. Dapper's default is 30s,
    /// which is fine for DDL but not for a backfill: 065 rewrites 1.6M rows
    /// of ibkr_price_bars (a 1.4GB table) and blew straight through it. The
    /// client gave up mid-statement, the transaction rolled back, the runner
    /// rethrew, and MigrationHostedService refused to start the app — so a
    /// slow migration took the whole API down and held it down, restart after
    /// restart. A migration that can't finish inside this window should be
    /// run out-of-band rather than at startup.
    /// </summary>
    private const int MigrationCommandTimeoutSeconds = 900;

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

            // A migration MUST NOT manage its own transaction — this runner owns
            // it. A stray BEGIN;/COMMIT; ends the runner's transaction, and the
            // resulting "transaction has completed" error points nowhere near
            // the file that caused it. Caught here, by name, BEFORE executing.
            //
            // Deliberately matches only a statement-level `BEGIN;` / `COMMIT;`
            // on its own line: PL/pgSQL function bodies legitimately contain
            // `BEGIN` (no semicolon) and `END;`, and must not trip this.
            var strayTx = System.Text.RegularExpressions.Regex.Match(
                body, @"^[ \t]*(BEGIN|COMMIT)[ \t]*;[ \t]*$",
                System.Text.RegularExpressions.RegexOptions.Multiline
                | System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            if (strayTx.Success)
                throw new InvalidOperationException(
                    $"Migration {name} contains its own '{strayTx.Groups[1].Value.ToUpperInvariant()};' "
                    + "statement. MigrationRunner already wraps each migration in a transaction; "
                    + "a nested COMMIT ends it and the runner's own commit then fails with a "
                    + "misleading 'transaction has completed' error. Remove the BEGIN;/COMMIT; "
                    + "from the .sql file.");

            _log.LogInformation("Applying migration {name}", name);
            await using var conn = new NpgsqlConnection(_connectionString);
            await conn.OpenAsync(ct);
            await using var tx = await conn.BeginTransactionAsync(ct);
            try
            {
                await conn.ExecuteAsync(
                    body, transaction: tx, commandTimeout: MigrationCommandTimeoutSeconds);
                await conn.ExecuteAsync(
                    "INSERT INTO schema_migrations (name, checksum) VALUES (@name, @checksum) ON CONFLICT (name) DO NOTHING",
                    new { name, checksum }, transaction: tx);
                await tx.CommitAsync(ct);
                _log.LogInformation("Migration {name} applied", name);
            }
            catch (Exception ex)
            {
                // LOG THE REAL FAILURE FIRST, and never let cleanup replace it.
                //
                // This used to call RollbackAsync BEFORE logging. When a
                // migration fails in a way that has already ended the
                // transaction — Postgres aborting it, or a failure after the
                // commit — the rollback itself throws "This NpgsqlTransaction
                // has completed; it is no longer usable", and THAT exception
                // replaced the original. `ex` was never logged and never
                // thrown, so the actual migration error was invisible.
                //
                // Cost of that: 108 of 371 backend tests failed with the
                // masking message and nobody could see why. I assumed for most
                // of 30 Aug 2026 that it meant "Postgres is not running" — it
                // was running the whole time. A safety-critical change (the
                // no-live-orders guard) went in against a suite whose failures
                // could not be read.
                _log.LogError(ex, "Migration {name} FAILED", name);
                try
                {
                    // Connection is null once the transaction has completed.
                    if (tx.Connection is not null)
                        await tx.RollbackAsync(ct);
                }
                catch (Exception rollbackEx)
                {
                    // Reported, never rethrown — a cleanup failure must not
                    // become the story.
                    _log.LogError(rollbackEx,
                        "Rollback ALSO failed for migration {name} (the failure above is the real one)",
                        name);
                }
                throw;   // the ORIGINAL exception, preserved
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
