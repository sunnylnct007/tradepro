using Npgsql;

namespace TradePro.Api.Data;

/// <summary>
/// One-stop DI registration for the Postgres data layer:
/// <list type="bullet">
///   <item>Binds <see cref="PostgresOptions"/> from configuration</item>
///   <item>Registers a singleton <see cref="NpgsqlDataSource"/> (the
///   modern Npgsql pool — preferred over the per-call connection
///   factory pattern)</item>
///   <item>Registers <see cref="MigrationRunner"/> so the hosted
///   service can resolve + invoke it on startup</item>
/// </list>
///
/// Note: this does NOT replace the in-memory / file-backed store
/// registrations — those still happen in Program.cs. Once a store
/// has a Postgres implementation, swap its registration there.
/// </summary>
public static class PostgresServiceExtensions
{
    public static IServiceCollection AddPostgresDataLayer(
        this IServiceCollection services, IConfiguration config)
    {
        services.Configure<PostgresOptions>(config.GetSection(PostgresOptions.SectionName));

        services.AddSingleton<NpgsqlDataSource>(sp =>
        {
            var opts = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<PostgresOptions>>().Value;
            var cs = config.GetConnectionString("Postgres") ?? opts.BuildConnectionString();
            return NpgsqlDataSource.Create(cs);
        });

        services.AddSingleton<MigrationRunner>(sp =>
        {
            var opts = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<PostgresOptions>>().Value;
            var cs = config.GetConnectionString("Postgres") ?? opts.BuildConnectionString();
            var path = opts.MigrationsPath
                ?? Path.Combine(AppContext.BaseDirectory, "db", "migrations");
            var log = sp.GetRequiredService<ILogger<MigrationRunner>>();
            return new MigrationRunner(cs, path, log);
        });

        services.AddHostedService<MigrationHostedService>();

        return services;
    }
}

/// <summary>
/// Runs the migration runner once at startup. Lives here (not in
/// MigrationRunner itself) so the runner stays a plain class that
/// can be unit-tested without the hosted-service machinery.
/// </summary>
internal sealed class MigrationHostedService : IHostedService
{
    private readonly MigrationRunner _runner;
    private readonly PostgresOptions _opts;
    private readonly ILogger<MigrationHostedService> _log;

    public MigrationHostedService(
        MigrationRunner runner,
        Microsoft.Extensions.Options.IOptions<PostgresOptions> opts,
        ILogger<MigrationHostedService> log)
    {
        _runner = runner;
        _opts = opts.Value;
        _log = log;
    }

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        if (!_opts.RunMigrationsOnStartup)
        {
            _log.LogInformation("Postgres migrations skipped (RunMigrationsOnStartup=false)");
            return;
        }
        try
        {
            await _runner.RunAsync(cancellationToken);
        }
        catch (Exception ex)
        {
            // Fail loudly. A boot that proceeds without migrations is
            // a boot that will half-work and confuse everyone later.
            _log.LogCritical(ex, "Postgres migrations failed — refusing to start");
            throw;
        }
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
