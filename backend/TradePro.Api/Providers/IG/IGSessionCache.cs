namespace TradePro.Api.Providers.IG;

/// <summary>
/// Process-wide cache of the IG session tokens (CST + X-SECURITY-TOKEN),
/// shared across every (transient) <see cref="IGClient"/> instance.
///
/// WHY THIS EXISTS: IGClient is registered via AddHttpClient&lt;T&gt;, which is
/// TRANSIENT — a fresh instance per injection. With the tokens held as
/// instance fields, every request (the cockpit polls /ig/positions every
/// 30s, plus every order) re-hit POST /session. That flood of logins from
/// one datacenter IP tripped IG's anti-abuse block ("request denied for
/// security purposes", HTTP 403 on /session). Holding the session in a
/// SINGLETON means we log in ~once per token lifetime (~6h) and reuse it,
/// cutting /session calls by orders of magnitude.
///
/// Thread-safe: <see cref="Lock"/> serialises the login so concurrent
/// callers don't all authenticate at once.
/// </summary>
public sealed class IGSessionCache
{
    // IG tokens live ~6h; refresh proactively at 5h so we never send a
    // just-expired token (which would 401 → re-login anyway).
    private static readonly TimeSpan Lifetime = TimeSpan.FromHours(5);

    // After a failed login, don't retry /session for this long. WITHOUT this,
    // a login failure (e.g. IG's anti-abuse 403 block) would make every
    // request re-hit /session — hammering IG and KEEPING the block alive.
    // Backing off lets IG's block clear and stops us re-triggering it.
    private static readonly TimeSpan FailureCooldown = TimeSpan.FromSeconds(60);

    public SemaphoreSlim Lock { get; } = new(1, 1);

    public string? Cst { get; private set; }
    public string? XSecurityToken { get; private set; }
    private DateTime _establishedUtc = DateTime.MinValue;
    private DateTime _lastFailureUtc = DateTime.MinValue;

    /// <summary>True when we hold non-null tokens that haven't aged out.</summary>
    public bool IsValid =>
        Cst is not null
        && XSecurityToken is not null
        && (DateTime.UtcNow - _establishedUtc) < Lifetime;

    /// <summary>False during the post-failure cooldown — caller should fail
    /// fast instead of hammering /session while IG is blocking us.</summary>
    public bool MayAttemptLogin =>
        (DateTime.UtcNow - _lastFailureUtc) >= FailureCooldown;

    public void Set(string cst, string xSecurityToken)
    {
        Cst = cst;
        XSecurityToken = xSecurityToken;
        _establishedUtc = DateTime.UtcNow;
        _lastFailureUtc = DateTime.MinValue;
    }

    /// <summary>Record a failed login so we back off before retrying.</summary>
    public void RecordFailure() => _lastFailureUtc = DateTime.UtcNow;

    /// <summary>Drop the tokens (on a 401 — force a re-login next call).</summary>
    public void Clear()
    {
        Cst = null;
        XSecurityToken = null;
        _establishedUtc = DateTime.MinValue;
    }
}
