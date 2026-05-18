namespace TradePro.Api.Data;

/// <summary>
/// Configuration for the Postgres data layer. Bound from
/// `ConnectionStrings:Postgres` (single string) or from individual
/// `Postgres:Host`, `Postgres:Port`, etc. fields when the connection
/// string isn't set.
///
/// The connection-string form wins when both are present — that's the
/// shape docker-compose injects and the most explicit for production.
/// The split form is convenient for local dev where you'd otherwise
/// have to remember the full Npgsql syntax.
/// </summary>
public sealed class PostgresOptions
{
    public const string SectionName = "Postgres";

    public string Host { get; set; } = "localhost";
    public int Port { get; set; } = 5432;
    public string Database { get; set; } = "tradepro";
    public string Username { get; set; } = "tradepro";
    public string Password { get; set; } = "tradepro";
    public int MinPoolSize { get; set; } = 1;
    public int MaxPoolSize { get; set; } = 20;

    /// <summary>If true, the migration runner is invoked on startup.
    /// Default true. Set to false in tests that want to apply
    /// migrations manually.</summary>
    public bool RunMigrationsOnStartup { get; set; } = true;

    /// <summary>Override path to the migrations directory. Defaults
    /// to <c>{AppContext.BaseDirectory}/db/migrations</c>.</summary>
    public string? MigrationsPath { get; set; }

    /// <summary>Build the Npgsql connection string from the split
    /// fields. Use this only when no explicit ConnectionStrings:Postgres
    /// is configured.</summary>
    public string BuildConnectionString() =>
        $"Host={Host};Port={Port};Database={Database};Username={Username};Password={Password};Pooling=true;MinPoolSize={MinPoolSize};MaxPoolSize={MaxPoolSize}";
}
