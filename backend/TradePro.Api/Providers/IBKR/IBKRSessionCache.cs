namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Process-wide cache of the IBKR OAuth2 bearer + brokerage session,
/// shared across every (transient) <see cref="IBKRClient"/> instance.
///
/// WHY THIS EXISTS (mirrors <see cref="IG.IGSessionCache"/>): the client is
/// registered via AddHttpClient&lt;T&gt; (TRANSIENT — a fresh instance per
/// injection). If the bearer + session lived as instance fields, every
/// request (positions poll, every order) would re-run the full OAuth2 +
/// sso-sessions + ssodh/init dance. IBKR explicitly rate-limits / blocks
/// that abuse pattern. Holding the bearer + session in a SINGLETON means
/// we authenticate ONCE per token lifetime and reuse it, keeping it warm
/// with a cheap /tickle every 60-90s instead of re-authing.
///
/// Thread-safe: <see cref="Lock"/> serialises the auth so concurrent
/// callers don't all authenticate at once.
/// </summary>
public sealed class IBKRSessionCache
{
    // OAuth2 access tokens commonly live ~24h; we refresh proactively well
    // before expiry. We don't assume a fixed lifetime — the token response
    // carries expires_in, so SetAuth records the server-supplied expiry and
    // we refresh at 80% of it (floored to a sane minimum).
    private static readonly TimeSpan DefaultLifetime = TimeSpan.FromHours(20);
    private static readonly TimeSpan FailureCooldown = TimeSpan.FromSeconds(60);

    public SemaphoreSlim Lock { get; } = new(1, 1);

    /// <summary>OAuth2 bearer from step 1 — sent on EVERY cpapi call.</summary>
    public string? AccessToken { get; private set; }

    /// <summary>Brokerage session token from step 2 / tickle session id.</summary>
    public string? SessionToken { get; private set; }

    /// <summary>True once ssodh/init + the 3-5s settle + /iserver/accounts
    /// have completed at least once this session — gates /iserver calls.</summary>
    public bool IserverReady { get; private set; }

    private DateTime _establishedUtc = DateTime.MinValue;
    private TimeSpan _lifetime = DefaultLifetime;
    private DateTime _lastFailureUtc = DateTime.MinValue;
    private DateTime _lastTickleUtc = DateTime.MinValue;

    /// <summary>True when we hold a non-null bearer that hasn't aged out
    /// (refresh at 80% of the server-supplied lifetime).</summary>
    public bool IsValid =>
        AccessToken is not null
        && (DateTime.UtcNow - _establishedUtc) < (_lifetime * 0.8);

    /// <summary>False during the post-failure cooldown — caller should fail
    /// fast instead of hammering IBKR while it's rate-limiting us.</summary>
    public bool MayAttemptAuth =>
        (DateTime.UtcNow - _lastFailureUtc) >= FailureCooldown;

    /// <summary>True when the session needs a keepalive tickle (older than
    /// the 60-90s window). Caller's background/lazy keepalive checks this.</summary>
    public bool NeedsTickle =>
        IsValid && (DateTime.UtcNow - _lastTickleUtc) >= TimeSpan.FromSeconds(75);

    public void SetAuth(string accessToken, int? expiresInSeconds)
    {
        AccessToken = accessToken;
        _establishedUtc = DateTime.UtcNow;
        _lifetime = expiresInSeconds is > 0
            ? TimeSpan.FromSeconds(expiresInSeconds.Value)
            : DefaultLifetime;
        _lastFailureUtc = DateTime.MinValue;
    }

    public void SetSession(string? sessionToken)
    {
        SessionToken = sessionToken;
        // Tickle resets the keepalive clock.
        _lastTickleUtc = DateTime.UtcNow;
    }

    public void MarkIserverReady() => IserverReady = true;
    public void MarkTickled() => _lastTickleUtc = DateTime.UtcNow;

    /// <summary>Record a failed auth so we back off before retrying.</summary>
    public void RecordFailure() => _lastFailureUtc = DateTime.UtcNow;

    /// <summary>Drop everything (on a 401 — force a full re-auth next call).</summary>
    public void Clear()
    {
        AccessToken = null;
        SessionToken = null;
        IserverReady = false;
        _establishedUtc = DateTime.MinValue;
        _lifetime = DefaultLifetime;
    }
}
