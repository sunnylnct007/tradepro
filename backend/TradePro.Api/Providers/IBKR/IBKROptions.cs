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
///   2. POST {OAuthBase}/gw/api/v1/sso-sessions — body is a SIGNED JWT
///      (Content-Type: application/jwt) whose claims carry
///      <see cref="Credential"/> (IBKR username) + the resolved egress IP;
///      Bearer the step-1 token. Returns a DIFFERENT access_token
///      (SSO_ACCESS) that is the bearer for all subsequent /v1/api calls.
///      (Per the IBKR Postman collection — a plain JSON body is rejected
///      with 400 "Invalid payload for security policy: SIGNED_JWT".)
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

    /// <summary>HARD kill-switch for order placement. Default FALSE
    /// (fail-safe): while we run IBKR READ-ONLY against a LIVE account, NO
    /// order may ever hit IBKR. When false, <see cref="IBKRClient.PlaceMarketOrderAsync"/>
    /// returns a rejected result WITHOUT building or sending any HTTP request,
    /// and the OMS IBKR dispatch branch skips placement entirely. It is absent
    /// from the <c>tradepro/ibkr</c> secret, so it binds to false — only an
    /// explicit <c>IBKR:AllowOrders=true</c> could ever enable order placement.</summary>
    public bool AllowOrders { get; set; } = false;

    // ─── OAuth 2.0 client-credentials (client-assertion JWT) ─────────
    //
    // IBKR issues a DIFFERENT client_id AND credential (brokerage login)
    // for paper vs live, while the RSA signing pair (private_key /
    // certificate / kid / public_key) and SourceIp are SHARED across both.
    // The active (clientId, credential, accountId) triple is selected by
    // <see cref="Mode"/> via the Active* resolvers below.

    /// <summary>BACKWARD-COMPAT single client_id. Used as a fallback by
    /// <see cref="ActiveClientId"/> when the per-env id for the active mode
    /// is empty. Prefer the per-env <see cref="ClientIdPaper"/> /
    /// <see cref="ClientIdLive"/> — those win when present.</summary>
    public string ClientId { get; set; } = string.Empty;

    /// <summary>Paper-env OAuth2 client_id. Selected (as iss/sub of the
    /// client-assertion JWT) when <see cref="Mode"/> != "live".</summary>
    public string ClientIdPaper { get; set; } = string.Empty;

    /// <summary>Live-env OAuth2 client_id. Selected when
    /// <see cref="Mode"/> == "live".</summary>
    public string ClientIdLive { get; set; } = string.Empty;

    /// <summary>Key id for the RSA signing key — emitted as the <c>kid</c>
    /// header of the client-assertion JWT so IBKR can pick the matching
    /// public key. SHARED across paper + live.</summary>
    public string ClientKeyId { get; set; } = string.Empty;

    /// <summary>BACKWARD-COMPAT single credential (IBKR username). Used as a
    /// fallback by <see cref="ActiveCredential"/> when the per-env
    /// credential for the active mode is empty. Prefer the per-env
    /// <see cref="CredentialPaper"/> / <see cref="CredentialLive"/>.</summary>
    public string Credential { get; set; } = string.Empty;

    /// <summary>Paper-env IBKR username (the brokerage login) — sent when
    /// opening the SSO brokerage session (step 2) in paper mode.</summary>
    public string CredentialPaper { get; set; } = string.Empty;

    /// <summary>Live-env IBKR username (the brokerage login) — sent when
    /// opening the SSO brokerage session (step 2) in live mode.</summary>
    public string CredentialLive { get; set; } = string.Empty;

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

    /// <summary>OPTIONAL override for the source IP IBKR expects the SSO
    /// session to originate from (step 2 body). When EMPTY (the recommended
    /// default), the backend AUTO-DETECTS its public egress IP at bring-up via
    /// <see cref="IBKREgressIpResolver"/> — so the operator can omit <c>ip</c>
    /// from the <c>tradepro/ibkr</c> secret and survive EC2 restarts that
    /// change the IP. Set it ONLY to pin a specific IP (override). Config-driven
    /// — never hardcoded. NOT required by <see cref="IsEnabled"/>.</summary>
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

    /// <summary>True when <see cref="Mode"/> == "live" (case-insensitive).
    /// Drives which per-env triple the Active* resolvers select.</summary>
    private bool IsLive =>
        string.Equals(Mode, "live", StringComparison.OrdinalIgnoreCase);

    // ─── Active (mode-resolved) credentials ──────────────────────────
    //
    // Pure resolvers: live → *Live, anything else (paper/disabled) →
    // *Paper, with the legacy single value as a fallback when the per-env
    // one is empty. The IBKRClient + status endpoint read ONLY these so
    // there is exactly one place that knows which environment is active.

    /// <summary>Client_id for the active mode: live → <see cref="ClientIdLive"/>,
    /// else <see cref="ClientIdPaper"/>; falls back to the legacy single
    /// <see cref="ClientId"/> when the per-env value is empty. Used as the
    /// iss/sub claim of the client-assertion JWT.</summary>
    public string ActiveClientId
    {
        get
        {
            var perEnv = IsLive ? ClientIdLive : ClientIdPaper;
            return string.IsNullOrWhiteSpace(perEnv) ? ClientId : perEnv;
        }
    }

    /// <summary>Credential (IBKR username) for the active mode: live →
    /// <see cref="CredentialLive"/>, else <see cref="CredentialPaper"/>;
    /// falls back to the legacy single <see cref="Credential"/> when the
    /// per-env value is empty. Sent when opening the SSO session.</summary>
    public string ActiveCredential
    {
        get
        {
            var perEnv = IsLive ? CredentialLive : CredentialPaper;
            return string.IsNullOrWhiteSpace(perEnv) ? Credential : perEnv;
        }
    }

    /// <summary>Brokerage account id for the active mode: live →
    /// <see cref="AccountIdLive"/>, else <see cref="AccountIdPaper"/>.
    /// (Account ids were already per-env; no legacy single key exists.)</summary>
    public string ActiveAccountId => IsLive ? AccountIdLive : AccountIdPaper;

    /// <summary>True only when a real (non-disabled) mode is set AND the
    /// minimum signing material for the ACTIVE environment is present
    /// (active clientId + active credential + private key + an active
    /// account id). Until <c>tradepro/ibkr</c> is populated this is false,
    /// so every IBKR method is a no-op and the OMS dispatch branch / health
    /// row report "disabled".</summary>
    public bool IsEnabled =>
        !string.Equals(Mode, "disabled", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(Mode)
        && !string.IsNullOrWhiteSpace(ActiveClientId)
        && !string.IsNullOrWhiteSpace(ClientKeyId)
        && !string.IsNullOrWhiteSpace(ActiveCredential)
        && !string.IsNullOrWhiteSpace(PrivateKey)
        && !string.IsNullOrWhiteSpace(ActiveAccountId);

    /// <summary>Account id selected by <see cref="Mode"/>. live → live id,
    /// anything else (paper/disabled) → paper id. Alias of
    /// <see cref="ActiveAccountId"/>, kept for existing call sites.</summary>
    public string AccountId => ActiveAccountId;

    /// <summary>Broker label stamped into oms_orders.broker so the OMS
    /// event log shows whether a fill came from IBKR paper vs live.
    /// Matches the validBrokers set (IBKR_PAPER / IBKR_LIVE).</summary>
    public string BrokerLabel =>
        string.Equals(Mode, "live", StringComparison.OrdinalIgnoreCase)
            ? "IBKR_LIVE"
            : "IBKR_PAPER";
}
