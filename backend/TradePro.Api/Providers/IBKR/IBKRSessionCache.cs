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

    /// <summary>STEP-1 OAuth2 token (TOKEN_ACCESS) from
    /// POST {oauth2Url}/api/v1/token. Used ONLY as the Bearer for the step-2
    /// POST /sso-sessions request — it is NOT the bearer for downstream
    /// /v1/api calls (see <see cref="AccessToken"/>). Per the IBKR Postman
    /// collection the two tokens are different.</summary>
    public string? TokenAccess { get; private set; }

    /// <summary>The WORKING bearer (SSO_ACCESS) returned by step-2
    /// POST /sso-sessions — sent on EVERY downstream cpapi call (tickle,
    /// ssodh/init, iserver/accounts, positions, ledger, orders). This is a
    /// DIFFERENT token from <see cref="TokenAccess"/>; the Postman collection
    /// is explicit that sso-sessions issues a new access_token that becomes
    /// the bearer for all subsequent requests.</summary>
    public string? AccessToken { get; private set; }

    /// <summary>Brokerage session id surfaced by /tickle (the <c>session</c>
    /// field). Held for diagnostics; the bearer for calls is
    /// <see cref="AccessToken"/> (SSO_ACCESS).</summary>
    public string? SessionToken { get; private set; }

    /// <summary>True once ssodh/init + the 3-5s settle + /iserver/accounts
    /// have completed at least once this session — gates /iserver calls.</summary>
    public bool IserverReady { get; private set; }

    /// <summary>Auto-detected public egress IP used for the sso-sessions
    /// <c>ip</c> claim, cached here so we detect ONCE per process (it only
    /// changes on restart / network change), not per request. Null until
    /// detected; dropped on <see cref="Clear"/> so a re-auth after a failure
    /// re-detects (the egress IP may have changed). Not set when an explicit
    /// IBKR:SourceIp override is in play.</summary>
    public string? DetectedEgressIp { get; private set; }

    private DateTime _establishedUtc = DateTime.MinValue;
    private TimeSpan _lifetime = DefaultLifetime;
    private DateTime _lastFailureUtc = DateTime.MinValue;
    private DateTime _lastTickleUtc = DateTime.MinValue;

    /// <summary>True when we hold the WORKING bearer (SSO_ACCESS) AND the
    /// step-1 token clock hasn't aged out (refresh at 80% of the
    /// server-supplied lifetime). Requires BOTH tokens so a half-completed
    /// bring-up (token but no sso-session) is treated as invalid.</summary>
    public bool IsValid =>
        AccessToken is not null
        && TokenAccess is not null
        && (DateTime.UtcNow - _establishedUtc) < (_lifetime * 0.8);

    /// <summary>False during the post-failure cooldown — caller should fail
    /// fast instead of hammering IBKR while it's rate-limiting us.</summary>
    public bool MayAttemptAuth =>
        (DateTime.UtcNow - _lastFailureUtc) >= FailureCooldown;

    /// <summary>True when the session needs a keepalive tickle (older than
    /// the 60-90s window). Caller's background/lazy keepalive checks this.</summary>
    public bool NeedsTickle =>
        IsValid && (DateTime.UtcNow - _lastTickleUtc) >= TimeSpan.FromSeconds(75);

    /// <summary>Record the STEP-1 token (TOKEN_ACCESS) + start the lifetime
    /// clock from the server-supplied expires_in. This token is only the
    /// bearer for the step-2 sso-sessions call, not for downstream calls.</summary>
    public void SetTokenAccess(string tokenAccess, int? expiresInSeconds)
    {
        TokenAccess = tokenAccess;
        _establishedUtc = DateTime.UtcNow;
        _lifetime = expiresInSeconds is > 0
            ? TimeSpan.FromSeconds(expiresInSeconds.Value)
            : DefaultLifetime;
        _lastFailureUtc = DateTime.MinValue;
    }

    /// <summary>Record the STEP-2 working bearer (SSO_ACCESS) returned by
    /// /sso-sessions — the token used for ALL downstream cpapi calls.</summary>
    public void SetSsoAccess(string ssoAccess)
    {
        AccessToken = ssoAccess;
    }

    public void SetSession(string? sessionToken)
    {
        SessionToken = sessionToken;
        // Tickle resets the keepalive clock.
        _lastTickleUtc = DateTime.UtcNow;
    }

    public void MarkIserverReady() => IserverReady = true;
    public void MarkTickled() => _lastTickleUtc = DateTime.UtcNow;

    /// <summary>Cache the auto-detected egress IP for reuse across the
    /// process / session lifetime.</summary>
    public void SetDetectedEgressIp(string ip) => DetectedEgressIp = ip;

    /// <summary>Record a failed auth so we back off before retrying.</summary>
    public void RecordFailure() => _lastFailureUtc = DateTime.UtcNow;

    /// <summary>Drop everything (on a 401 — force a full re-auth next call).</summary>
    public void Clear()
    {
        TokenAccess = null;
        AccessToken = null;
        SessionToken = null;
        IserverReady = false;
        // Drop the cached egress IP so a re-auth (after a failure / restart)
        // re-detects it — the host's public IP may have changed.
        DetectedEgressIp = null;
        _establishedUtc = DateTime.MinValue;
        _lifetime = DefaultLifetime;
    }
}
