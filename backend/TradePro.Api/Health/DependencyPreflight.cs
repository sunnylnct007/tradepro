using Microsoft.Extensions.Options;
using Npgsql;
using TradePro.Api.Auth;
using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Health;

/// <summary>
/// Verifies, once at startup, that the outside world is actually reachable —
/// and records the verdict where a monitor can read it.
///
/// The gap this closes: on 16 Sep 2026 a routine redeploy was the first
/// container restart in days. The EC2 role had drifted and could no longer read
/// tradepro/ibkr, so the whole broker integration came up disabled. Nothing
/// crashed. /health said "ok". The only evidence was three IAM errors in
/// container logs. The desk looked healthy and was not.
///
/// WHY STARTUP IS THE RIGHT MOMENT: secrets are read exactly once, at boot. A
/// permission that is only exercised at startup is untested until something
/// restarts — which is why this went unnoticed for months while containers
/// happily ran on values fetched before the drift.
///
/// This NEVER prevents the app from serving. See DependencyReport for why
/// (short version: /health is the Docker healthcheck, so failing it would
/// restart-loop the container).
/// </summary>
public sealed class DependencyPreflight : IHostedService
{
    /// <summary>
    /// Secrets whose absence silently disables a capability. Mirrors what
    /// SecretsBundleLoader actually fetches AND the IAM grant in
    /// ccit-infra var.runtime_secret_names. Three places, which is two too
    /// many — but at least a drift between them now SHOWS UP rather than
    /// waiting for the next restart to bite.
    /// </summary>
    private static readonly string[] RequiredSecrets = { "tradepro/all", "tradepro/ibkr" };

    private readonly DependencyReport _report;
    private readonly IConfiguration _config;
    private readonly NpgsqlDataSource _db;
    private readonly IBKROptions _ibkr;
    private readonly ILogger<DependencyPreflight> _log;

    public DependencyPreflight(
        DependencyReport report,
        IConfiguration config,
        // The app's OWN data source, not a connection string rebuilt here.
        // Re-deriving it would mean this check could pass against a different
        // database than the one the app uses — duplicate definitions are this
        // codebase's most common bug shape, and a health check that verifies
        // the wrong thing is worse than none.
        NpgsqlDataSource db,
        IOptions<IBKROptions> ibkr,
        ILogger<DependencyPreflight> log)
    {
        _report = report;
        _config = config;
        _db = db;
        _ibkr = ibkr.Value;
        _log = log;
    }

    public async Task StartAsync(CancellationToken ct)
    {
        // NOTHING in here may throw. A hosted service that throws from
        // StartAsync ABORTS HOST STARTUP — the container would never come up,
        // Docker would restart-loop it, and a diagnostic would have caused a
        // total outage. That is the same shape as 13 Sep, when one unpullable
        // image took the whole site down. Observability must not be able to
        // break the thing it observes.
        try
        {
            CheckSecrets();
            await CheckDatabaseAsync(ct);
            CheckIbkr();
        }
        catch (Exception ex)
        {
            // The preflight itself is broken. Say so as a dependency rather
            // than dying: an unrunnable check is its own kind of blind spot.
            _report.Record("preflight", false,
                $"the preflight itself threw: {ex.Message}");
            _log.LogError(ex, "preflight threw; continuing startup regardless");
        }
        _report.MarkChecked(DateTime.UtcNow);

        var failing = _report.Failing;
        if (failing.Count == 0)
        {
            _log.LogInformation(
                "preflight: all {N} dependencies OK", _report.All.Count);
            return;
        }

        // One line per failure, at Error, naming the dependency and the cause.
        // The previous failure was not a missing log — it was a log nobody
        // correlated. /health carries the same verdict for machines.
        foreach (var f in failing)
            _log.LogError(
                "preflight FAILED: {Name} — {Detail}. A capability is disabled; "
                + "/health now reports status=degraded.",
                f.Name, f.Detail);
    }

    public Task StopAsync(CancellationToken ct) => Task.CompletedTask;

    private void CheckSecrets()
    {
        var outcomes = SecretsBundleLoader.FetchOutcomes;
        foreach (var name in RequiredSecrets)
        {
            if (outcomes.TryGetValue(name, out var o))
            {
                _report.Record($"secret:{name}", o.Ok, o.Detail);
                continue;
            }
            // No outcome at all: the loader never reached it. Reported as
            // unknown-but-not-failing ONLY when secrets are deliberately
            // disabled for local runs; otherwise it IS a failure, because a
            // required secret that was never even attempted is exactly the
            // silent hole this class exists to surface.
            var disabled = string.Equals(
                _config["Secrets:BundleDisabled"], "true", StringComparison.OrdinalIgnoreCase);
            _report.Record($"secret:{name}", disabled,
                disabled
                    ? "skipped — Secrets:BundleDisabled=true (local/dev)"
                    : "never attempted — the loader did not reach this secret",
                critical: !disabled);
        }
    }

    private async Task CheckDatabaseAsync(CancellationToken ct)
    {
        try
        {
            await using var conn = await _db.OpenConnectionAsync(ct);
            await using var cmd = new NpgsqlCommand("SELECT 1", conn);
            await cmd.ExecuteScalarAsync(ct);
            _report.Record("database", true, "reachable");
        }
        catch (Exception ex)
        {
            _report.Record("database", false, ex.Message);
        }
    }

    private void CheckIbkr()
    {
        // Config presence ONLY — this deliberately does not authenticate or
        // request a snapshot. Boot is the worst moment to open a session: the
        // desk shares ONE market-data session and a startup handshake would
        // contend with whatever is mid-pass. Whether auth actually succeeds is
        // the running health probe's job; whether we are even configured to try
        // is this one's, and that is the thing that broke.
        if (_ibkr.IsEnabled)
        {
            _report.Record("ibkr:config", true, $"enabled, mode={_ibkr.Mode}");
            return;
        }
        _report.Record("ibkr:config", false,
            "IBKR disabled — credentials absent. Usually the tradepro/ibkr secret "
            + "failed to load (check secret:tradepro/ibkr above), not a code setting.");
    }
}
