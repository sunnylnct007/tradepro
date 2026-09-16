using System.Collections.Concurrent;

namespace TradePro.Api.Health;

/// <summary>One dependency's verdict.</summary>
/// <param name="Name">Stable identifier, e.g. "secret:tradepro/ibkr".</param>
/// <param name="Ok">False when the dependency is unusable.</param>
/// <param name="Detail">Why — shown to a human, so name the actual cause.</param>
/// <param name="Critical">
/// True when losing this SILENTLY DISABLES A CAPABILITY. Non-critical entries
/// are recorded for context but never move the overall verdict.
/// </param>
public sealed record DependencyCheck(string Name, bool Ok, string Detail, bool Critical = true);

/// <summary>
/// What this process actually verified about the outside world, and when.
///
/// WHY IT EXISTS. /health returned a hardcoded `status: "ok"` that could not
/// fail while the process was alive. On 16 Sep 2026 it reported ok all
/// afternoon while the IBKR integration was completely disabled: the EC2 role
/// had lost permission to read tradepro/ibkr, the loader logged the denial and
/// CONTINUED with defaults, and nothing anywhere contradicted "ok". The
/// information existed — three IAM errors in `docker logs` — it simply lived
/// where nothing was watching. Logging is not shouting.
///
/// This is the structured verdict a monitor can actually read.
///
/// WHAT IT DELIBERATELY DOES NOT DO: refuse to serve. /health is the Docker
/// HEALTHCHECK for both the api and frontend containers (`curl -fsS`, which
/// fails on any non-2xx), and the frontend gates on the api being healthy. A
/// degraded verdict answering 503 would restart-loop the API and take the site
/// down — the preflight causing the outage it exists to reveal. The endpoint
/// stays 200; the verdict is in the BODY, where `status` flips to "degraded".
///
/// Refusing the ACTION is a separate concern and belongs at the action. The
/// reconciler already models it exactly right, and kept doing so through the
/// 16 Sep outage:
///
///   reconcile IBKR_PAPER: golden-source read failed (IBKR disabled)
///     — NOT settling (a failed read is not 'flat')
///
/// It did not crash and it did not conclude the book was flat. That is the
/// pattern to copy for anything that can act on missing data; this class only
/// makes the state visible.
/// </summary>
public sealed class DependencyReport
{
    private readonly ConcurrentDictionary<string, DependencyCheck> _checks = new();

    /// <summary>When the process last completed a preflight pass.</summary>
    public DateTime? LastCheckedUtc { get; private set; }

    public void Record(DependencyCheck check) => _checks[check.Name] = check;

    public void Record(string name, bool ok, string detail, bool critical = true)
        => Record(new DependencyCheck(name, ok, detail, critical));

    public void MarkChecked(DateTime utc) => LastCheckedUtc = utc;

    public IReadOnlyList<DependencyCheck> All =>
        _checks.Values.OrderBy(c => c.Name, StringComparer.Ordinal).ToList();

    /// <summary>Critical dependencies currently failing.</summary>
    public IReadOnlyList<DependencyCheck> Failing =>
        _checks.Values.Where(c => !c.Ok && c.Critical)
                      .OrderBy(c => c.Name, StringComparer.Ordinal).ToList();

    /// <summary>
    /// "ok" or "degraded". Deliberately NOT "unhealthy": the process is serving
    /// correctly for everything that still works, and the word a monitor reads
    /// should describe the desk, not scare someone into restarting a container
    /// that is fine.
    ///
    /// Unknown is reported as "ok". A preflight that has not run yet must not
    /// invent a fault — a false alarm here trains people to ignore this field,
    /// which is precisely how the real signal got lost last time.
    /// </summary>
    public string Status => Failing.Count > 0 ? "degraded" : "ok";
}
