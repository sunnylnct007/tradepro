namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// IBKR (Interactive Brokers) broker configuration. Values come from
/// AWS Secrets Manager under the standalone <c>tradepro/ibkr</c> secret
/// (snake_case keys → IBKR:* config, mapped in
/// <see cref="Auth.SecretsBundleLoader"/>) — kept out of code + out of
/// git per the secrets pattern the rest of the providers follow.
///
/// Bound from Configuration in Program.cs (which merges the SM loader
/// so the IBKR secret values flow in via the same chain as IG + T212).
///
/// OAuth 2.0 Web API flow (as specified by IBKR support):
///   1. POST {OAuthBase}/oauth2/api/v1/token  (scope=sso-sessions.write)
///      authenticated with a CLIENT-ASSERTION JWT (RS256) signed by the
///      RSA private key; JWT header carries <see cref="ClientKeyId"/> as
///      the kid, claims carry <see cref="ClientId"/> as iss/sub. Returns
///      an OAuth2 access token (bearer).
///   2. POST {OAuthBase}/gw/api/v1/sso-sessions — body carries
///      <see cref="Credential"/> (IBKR username) + <see cref="SourceIp"/>;
///      Bearer the access token. Returns a brokerage session.
///   3. POST {ApiBase}/v1/api/tickle — retrieve session / sessionToken.
///   4. POST {ApiBase}/v1/api/iserver/auth/ssodh/init — required before
///      any /iserver endpoint.
///   5. sleep 3-5s.
///   6. GET {ApiBase}/v1/api/iserver/accounts.
///   8. Keepalive: POST /v1/api/tickle every 60-90s (NEVER re-auth per
///      request — IBKR will rate-limit / block abuse). See IBKRSessionCache.
///   9. POST /v1/api/logout to close.
/// </summary>
public sealed class IBKROptions
{
    public const string SectionName = "IBKR";

    /// <summary>"live" / "paper" / "disabled". Disabled = no IBKR calls
    /// happen; the client is constructed but every method is a no-op so
    /// the rest of the app still compiles + boots without IBKR creds
    /// configured. Defaults to disabled until <c>tradepro/ibkr</c> lands.</summary>
    public string Mode { get; set; } = "disabled";

    // ─── OAuth 2.0 client-credentials (client-assertion JWT) ─────────

    /// <summary>OAuth2 client_id — also used as the iss + sub claim in the
    /// client-assertion JWT.</summary>
    public string ClientId { get; set; } = string.Empty;

    /// <summary>Key id for the RSA signing key — emitted as the <c>kid</c>
    /// header of the client-assertion JWT so IBKR can pick the matching
    /// public key.</summary>
    public string ClientKeyId { get; set; } = string.Empty;

    /// <summary>IBKR username (the brokerage login) — sent when opening the
    /// SSO brokerage session (step 2).</summary>
    public string Credential { get; set; } = string.Empty;

    /// <summary>RSA private key (PEM) used to RS256-sign the client
    /// assertion. NEVER logged. From Secrets Manager only.</summary>
    public string PrivateKey { get; set; } = string.Empty;

    /// <summary>RSA public key (PEM). Held for completeness / rotation
    /// tooling; not required for signing.</summary>
    public string PublicKey { get; set; } = string.Empty;

    /// <summary>PEM X.509 certificate IBKR's OAuth2 self-service issues for
    /// the RSA signing pair (you upload it to their portal to obtain the
    /// client_id + client_key_id). Optional at runtime: signing stays
    /// kid-based (the correct default for IBKR private_key_jwt). Held here
    /// so that, if IBKR's setup later requires the cert inline, we can flip
    /// <see cref="UseX5c"/> on and embed it as an x5c JWT header WITHOUT a
    /// code change. From Secrets Manager only.</summary>
    public string Certificate { get; set; } = string.Empty;

    /// <summary>When true (and a parseable <see cref="Certificate"/> is
    /// present), include the cert as a base64-DER <c>x5c</c> entry in the
    /// client-assertion JWT header IN ADDITION to <c>kid</c>. Default false:
    /// IBKR's documented private_key_jwt flow keys off the kid, so we ship
    /// kid-only and leave x5c as a config-flippable escape hatch.</summary>
    public bool UseX5c { get; set; } = false;

    /// <summary>Source IP IBKR expects the SSO session to originate from
    /// (step 2 body). Config-driven — never hardcoded.</summary>
    public string SourceIp { get; set; } = string.Empty;

    // ─── Account routing (config-driven, never hardcoded) ────────────

    /// <summary>Live brokerage account id (e.g. U2512...). Selected when
    /// <see cref="Mode"/> == "live".</summary>
    public string AccountIdLive { get; set; } = string.Empty;

    /// <summary>Paper brokerage account id (e.g. DUP6...). Selected when
    /// <see cref="Mode"/> == "paper".</summary>
    public string AccountIdPaper { get; set; } = string.Empty;

    // ─── Endpoint bases (overridable for tests; sane prod defaults) ──

    /// <summary>OAuth2 + SSO-session gateway host. Steps 1 + 2 hit this.</summary>
    public string OAuthBaseUrl { get; set; } = "https://api.ibkr.com";

    /// <summary>Client Portal (cpapi) host. Steps 3-9 hit this.</summary>
    public string ApiBaseUrl { get; set; } = "https://api.ibkr.com";

    /// <summary>True only when a real (non-disabled) mode is set AND the
    /// minimum signing material is present. Until <c>tradepro/ibkr</c> is
    /// populated this is false, so every IBKR method is a no-op and the
    /// OMS dispatch branch / health row report "disabled".</summary>
    public bool IsEnabled =>
        !string.Equals(Mode, "disabled", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(ClientId)
        && !string.IsNullOrWhiteSpace(ClientKeyId)
        && !string.IsNullOrWhiteSpace(Credential)
        && !string.IsNullOrWhiteSpace(PrivateKey);

    /// <summary>Account id selected by <see cref="Mode"/>. live → live id,
    /// anything else (paper/disabled) → paper id. Pure + unit-tested.</summary>
    public string AccountId =>
        string.Equals(Mode, "live", StringComparison.OrdinalIgnoreCase)
            ? AccountIdLive
            : AccountIdPaper;

    /// <summary>Broker label stamped into oms_orders.broker so the OMS
    /// event log shows whether a fill came from IBKR paper vs live.
    /// Matches the validBrokers set (IBKR_PAPER / IBKR_LIVE).</summary>
    public string BrokerLabel =>
        string.Equals(Mode, "live", StringComparison.OrdinalIgnoreCase)
            ? "IBKR_LIVE"
            : "IBKR_PAPER";
}
