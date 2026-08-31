using System.Collections.Concurrent;
using System.Linq;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// IBKR (Interactive Brokers) OAuth2 Web API client. Single class because
/// the IBKR surface we touch is small (auth dance, positions, cash, place
/// order) and the OAuth2-specific oddities (client-assertion JWT, the
/// multi-step session bring-up, the ssodh/init gate) are best kept in one
/// place rather than spread across files.
///
/// SESSION MODEL (singleton cache — see <see cref="IBKRSessionCache"/>):
/// the bearer + brokerage session are established ONCE and reused. We do
/// NOT re-auth per request (IBKR rate-limits / blocks that). The cheap
/// /tickle keepalive holds the session warm. On HTTP 401 we clear the
/// cache + re-auth once + retry.
///
/// Until the <c>tradepro/ibkr</c> secret is populated, <see cref="IsEnabled"/>
/// is false and every method is a safe no-op so the rest of the app boots.
/// The HTTP base address is only set when enabled, so a disabled client
/// can't accidentally hit the network.
/// </summary>
public sealed class IBKRClient
{
    // symbol|secType → conid. Conids are stable for the life of a listing, yet
    // before this cache EVERY price-history/quote/snapshot call re-ran
    // /iserver/secdef/search — measured 21 Aug 2026 as ~half of ALL IBKR
    // traffic (~200k requests/day system-wide), the pacing budget the options
    // screen then starved behind. Static because the typed HttpClient makes
    // this class transient; process-lifetime is the safety valve (an API
    // restart clears it after a corporate action re-plumbs a listing).
    // Positive results only — a transient search failure must not poison it.
    // Order placement deliberately bypasses this cache (real-money path).
    private static readonly ConcurrentDictionary<string, long> _conidCache = new();

    // Yahoo-style class-share notation → IBKR notation for secdef/search:
    // BRK-B / BF-B are "BRK B" / "BF B" at IBKR. Only for STK lookups —
    // FX pairs and other secTypes never carry this convention.
    private static string SearchSymbol(string sym, string secType) =>
        secType == "STK" ? sym.Replace('-', ' ') : sym;

    // Option-chain resolution caches (22 Aug 2026). The wheel screen ran
    // ~2,900 IBKR requests per sweep and ~1,700 of them re-resolved the SAME
    // static facts twice a day: available months (secdef/search), strikes
    // per month (secdef/strikes), and — one call per strike — the contract
    // conid (secdef/info). Months/strikes get a 12h TTL (listings change
    // slowly, weeklies appear overnight); a resolved CONTRACT is immutable
    // for its life, so those cache without expiry. Successes only; the
    // process restart on deploy is the flush valve.
    private static readonly TimeSpan _chainMetaTtl = TimeSpan.FromHours(12);
    private static readonly ConcurrentDictionary<string, (DateTime AtUtc, IBKROptionMonthsResult R)> _monthsCache = new();
    private static readonly ConcurrentDictionary<string, (DateTime AtUtc, IBKROptionStrikesResult R)> _strikesCache = new();
    private static readonly ConcurrentDictionary<string, IBKROptionContractsResult> _optContractCache = new();

    private readonly HttpClient _http;
    private readonly IBKROptions _options;
    private readonly IBKRSessionCache _session;
    private readonly IBKREgressIpResolver _ipResolver;
    private readonly IBKRPauseState _pause;
    private readonly ILogger<IBKRClient> _log;

    public IBKRClient(
        HttpClient http,
        IOptions<IBKROptions> options,
        IBKRSessionCache session,
        IBKREgressIpResolver ipResolver,
        IBKRPauseState pause,
        ILogger<IBKRClient> log)
    {
        _http = http;
        _options = options.Value;
        _session = session;
        _ipResolver = ipResolver;
        _pause = pause;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.ApiBaseUrl.TrimEnd('/') + "/");
            // Headers IBKR's Web API requires on EVERY request (it rejects the
            // call otherwise) — verbatim from the IBKR Postman collection's
            // common request headers. Host is set automatically by HttpClient.
            var h = _http.DefaultRequestHeaders;
            h.Accept.Clear();
            h.Accept.ParseAdd("*/*");
            h.AcceptEncoding.Clear();
            h.AcceptEncoding.ParseAdd("gzip, deflate");
            h.Connection.Clear();
            h.Connection.ParseAdd("keep-alive");
            h.UserAgent.Clear();
            h.UserAgent.ParseAdd("tradepro/1.0");
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string BrokerLabel => _options.BrokerLabel;
    public string AccountId => _options.AccountId;

    /// <summary>HARD kill-switch state (reflects <see cref="IBKROptions.AllowOrders"/>).
    /// FALSE by default — order placement is disabled. Both the OMS dispatch
    /// guard and the /integrations/ibkr/status endpoint read this so the
    /// read-only guarantee is visible + auditable. Only an explicit
    /// IBKR:AllowOrders=true flips it.</summary>
    /// <remarks>Since 30 Aug 2026 this is BOTH keys, not one. Live placement
    /// additionally requires <see cref="IBKROptions.AllowLiveOrders"/>, so
    /// flipping Mode to "live" is NOT sufficient to place an order. Every
    /// existing call site (the OMS dispatch branch, the two integration
    /// endpoints, the status endpoint) reads this property, so the guard is
    /// inherited everywhere rather than repeated — and cannot be forgotten at
    /// a new call site.</remarks>
    public bool AllowOrders
    {
        get
        {
            // NO ORDER PLACEMENT TO LIVE. AT ALL. Owner, 30 Aug 2026: "just
            // remember no order placement to live unless we change it", then
            // immediately firmer — "no placement3 to live at all".
            //
            // This started as a two-key opt-in (AllowOrders + AllowLiveOrders).
            // The owner overruled that, and he is right: an opt-in key is a
            // thing that can be set by accident, by a copied secret, or by
            // someone who does not know why it exists. There is now NO key to
            // flip. Enabling live placement requires editing this method, which
            // means a diff, a review and a deploy — the friction is the point.
            //
            // Checked FIRST so no combination of other flags can reach past it.
            if (_options.IsLiveMode) return false;
            return _options.AllowOrders;    // paper: the normal kill-switch
        }
    }

    /// <summary>True when orders are blocked because the account is LIVE.
    /// Surfaced by /integrations/ibkr/status so the reason is legible rather
    /// than looking like a generic kill-switch someone might "helpfully" turn
    /// back on.</summary>
    public bool BlockedForLive => _options.IsLiveMode;

    /// <summary>The IP that actually went into the last sso-sessions claim,
    /// for operator visibility (surfaced by /integrations/ibkr/status). Null
    /// until a bring-up has resolved one.</summary>
    public string? LastResolvedIp { get; private set; }

    /// <summary>How <see cref="LastResolvedIp"/> was obtained: "override"
    /// (IBKR:SourceIp set) or "auto-detected" (egress probe). Null until
    /// resolved.</summary>
    public string? LastResolvedIpSource { get; private set; }

    // ─── Auth (steps 1-6) ───────────────────────────────────────────

    /// <summary>
    /// Run the full bring-up if we don't already hold a valid session:
    ///   1. token (client-assertion JWT)
    ///   2. sso-sessions
    ///   3. tickle (session id)
    ///   4. iserver/auth/ssodh/init
    ///   5. settle 3-5s
    ///   6. iserver/accounts
    /// Thread-safe via the singleton lock; concurrent callers reuse the
    /// session the first winner establishes. No-op when disabled.
    /// </summary>
    private async Task EnsureSessionAsync(CancellationToken ct)
    {
        if (!_options.IsEnabled) return;
        // Operator paused IBKR to reclaim the single Web-API session for the portal.
        // Refuse to (re)establish OR keep-alive a session so TradePro can't grab the
        // account back while the user is in the IBKR portal. Throwing makes EVERY
        // authed call fail fast; callers already degrade (Yahoo fallback / skip).
        if (_pause.Paused)
            throw new InvalidOperationException(
                "IBKR is PAUSED by the operator — the Web-API session is released for portal use. Resume to re-enable.");
        if (_session.IsValid && _session.IserverReady)
        {
            await KeepAliveAsync(ct);
            return;
        }

        await _session.Lock.WaitAsync(ct);
        try
        {
            if (_session.IsValid && _session.IserverReady) return;
            if (!_session.MayAttemptAuth)
                throw new InvalidOperationException(
                    "IBKR auth backing off after a recent failure (cooldown) — not re-authing yet");

            // ── 1. OAuth2 token via client-assertion JWT ──
            // Endpoint per the Postman collection: {oauth2Url}/api/v1/token,
            // i.e. https://api.ibkr.com/oauth2/api/v1/token. The assertion's
            // aud claim is the LITERAL "/token" (TokenAudience), NOT this URL.
            var tokenEndpoint = _options.OAuthBaseUrl.TrimEnd('/') + "/oauth2/api/v1/token";
            string assertion;
            using (var rsa = IBKRClientAssertion.ImportPem(_options.PrivateKey))
            {
                assertion = IBKRClientAssertion.Build(
                    rsa,
                    // ACTIVE (mode-resolved) client_id — paper vs live use
                    // different ids; the kid/private_key signing pair is shared.
                    clientId: _options.ActiveClientId,
                    clientKeyId: _options.ClientKeyId,
                    // aud = "/token" (literal) per the IBKR Postman collection.
                    audience: IBKRClientAssertion.TokenAudience,
                    // kid stays the default selector; x5c is only embedded
                    // when the operator flips IBKR:UseX5c on (config-driven,
                    // no code change) and the cert parses.
                    certificatePem: _options.Certificate,
                    includeX5c: _options.UseX5c);
            }
            using (var tokenReq = new HttpRequestMessage(HttpMethod.Post, tokenEndpoint))
            {
                tokenReq.Content = new FormUrlEncodedContent(new Dictionary<string, string>
                {
                    ["grant_type"] = "client_credentials",
                    ["scope"] = IBKRClientAssertion.SsoSessionsWriteScope,
                    ["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    ["client_assertion"] = assertion,
                });
                using var tokenResp = await _http.SendAsync(tokenReq, ct);
                var tokenText = await tokenResp.Content.ReadAsStringAsync(ct);
                if (!tokenResp.IsSuccessStatusCode)
                {
                    _session.RecordFailure();
                    throw new InvalidOperationException(
                        $"IBKR /oauth2/token failed {(int)tokenResp.StatusCode}: {tokenText}");
                }
                var token = IBKRResponseParser.ParseToken(tokenText);
                if (string.IsNullOrEmpty(token.AccessToken))
                {
                    _session.RecordFailure();
                    throw new InvalidOperationException("IBKR /oauth2/token returned no access_token");
                }
                // TOKEN_ACCESS — bearer ONLY for the step-2 sso-sessions call.
                _session.SetTokenAccess(token.AccessToken, token.ExpiresInSeconds);
            }

            // ── 2. sso-sessions (credential + ip) ──
            // Resolve the ip claim: explicit IBKR:SourceIp override wins;
            // otherwise auto-detect the backend's public egress IP (cached on
            // the session). Throws a clear error if neither is available so we
            // never send an empty ip. Records what was used for /status.
            string resolvedIp, ipSource;
            try
            {
                (resolvedIp, ipSource) =
                    await _ipResolver.ResolveAsync(_options.SourceIp, _session, ct);
            }
            catch (InvalidOperationException)
            {
                // Egress IP unresolvable + no override — back off, then rethrow
                // so GetStatusAsync surfaces the clear "set IBKR:SourceIp" hint.
                _session.RecordFailure();
                throw;
            }
            LastResolvedIp = resolvedIp;
            LastResolvedIpSource = ipSource;

            // THE FIX: IBKR's gateway rejects a JSON body here with
            // 400 "Invalid payload for security policy: SIGNED_JWT". Per the
            // IBKR Postman collection ("Create SSO Session"), the body must be
            // a RAW signed JWT (JWS) — Content-Type: application/jwt — whose
            // claims are { ip, credential, iss, exp=now+86400, iat=now } with
            // NO aud / NO sub. We sign it with the SAME RS256 key + kid as the
            // step-1 assertion, and Bearer it with TOKEN_ACCESS (step-1 token).
            var ssoEndpoint = _options.OAuthBaseUrl.TrimEnd('/') + "/gw/api/v1/sso-sessions";
            string ssoJwt;
            using (var rsa = IBKRClientAssertion.ImportPem(_options.PrivateKey))
            {
                ssoJwt = IBKRClientAssertion.BuildSsoSession(
                    rsa,
                    clientId: _options.ActiveClientId,
                    clientKeyId: _options.ClientKeyId,
                    // ACTIVE (mode-resolved) credential — paper vs live login.
                    credential: _options.ActiveCredential,
                    ip: resolvedIp);
            }
            using (var ssoReq = new HttpRequestMessage(HttpMethod.Post, ssoEndpoint))
            {
                // Bearer the STEP-1 token (TOKEN_ACCESS) for the sso-sessions call.
                ssoReq.Headers.Authorization =
                    new AuthenticationHeaderValue("Bearer", _session.TokenAccess);
                // Raw signed-JWT body with Content-Type: application/jwt.
                ssoReq.Content = new StringContent(ssoJwt);
                ssoReq.Content.Headers.ContentType =
                    new MediaTypeHeaderValue("application/jwt");
                using var ssoResp = await _http.SendAsync(ssoReq, ct);
                var ssoText = await ssoResp.Content.ReadAsStringAsync(ct);
                if (!ssoResp.IsSuccessStatusCode)
                {
                    _session.RecordFailure();
                    throw new InvalidOperationException(
                        $"IBKR /sso-sessions failed {(int)ssoResp.StatusCode}: {ssoText}");
                }
                var sso = IBKRResponseParser.ParseSsoSession(ssoText);
                if (string.IsNullOrEmpty(sso.SessionToken))
                {
                    _session.RecordFailure();
                    throw new InvalidOperationException("IBKR /sso-sessions returned no access_token");
                }
                // SSO_ACCESS — the DIFFERENT token that is the bearer for ALL
                // downstream /v1/api calls (tickle, ssodh/init, accounts, ...).
                _session.SetSsoAccess(sso.SessionToken);
            }

            // ── 3. tickle (retrieve session id) ──
            await TickleAsync(ct);

            // ── 4. iserver/auth/ssodh/init (required before /iserver) ──
            using (var initReq = BuildAuthed(HttpMethod.Post, "v1/api/iserver/auth/ssodh/init"))
            {
                initReq.Content = JsonContent.Create(new { publish = true, compete = true });
                using var initResp = await _http.SendAsync(initReq, ct);
                var initText = await initResp.Content.ReadAsStringAsync(ct);
                if (!initResp.IsSuccessStatusCode)
                {
                    _session.RecordFailure();
                    throw new InvalidOperationException(
                        $"IBKR /iserver/auth/ssodh/init failed {(int)initResp.StatusCode}: {initText}");
                }

                // ASSERT ON THE BODY, NOT THE STATUS CODE.
                //
                // ssodh/init returns {authenticated, competing, connected, ...} and a
                // 200 says only that IBKR accepted the request — NOT that this session
                // won the competition for the account's single market-data session. We
                // send compete:true and then never read whether it worked, so a lost
                // race is indistinguishable from a won one all the way down to a dark
                // snapshot with no error code attached.
                //
                // That ambiguity is the whole reason the health probe can only report
                // "auth VALID but snapshot DARK — contention or IBKR-side outage": it
                // has authenticated and nothing else to go on. Recording these three
                // flags turns that into an answer.
                try
                {
                    using var initDoc = System.Text.Json.JsonDocument.Parse(initText);
                    var r = initDoc.RootElement;
                    bool Flag(string n) => r.TryGetProperty(n, out var v)
                        && v.ValueKind == System.Text.Json.JsonValueKind.True;
                    LastAuthenticated = Flag("authenticated");
                    LastCompeting = Flag("competing");
                    LastConnected = Flag("connected");
                    LastAuthStatusRaw = initText.Length > 400 ? initText[..400] : initText;
                    LastAuthStatusAtUtc = DateTime.UtcNow;

                    if (LastCompeting != true || LastConnected != true)
                    {
                        _log.LogWarning(
                            "IBKR ssodh/init returned authenticated={Auth} competing={Comp} "
                            + "connected={Conn} — the brokerage session did NOT take the "
                            + "market-data slot. Live quotes will be dark while historical "
                            + "bars may still answer. Raw: {Raw}",
                            LastAuthenticated, LastCompeting, LastConnected, LastAuthStatusRaw);
                    }
                }
                catch (System.Text.Json.JsonException)
                {
                    LastAuthStatusRaw = "unparseable: " + (initText.Length > 200 ? initText[..200] : initText);
                }
            }

            // ── 5. settle: brokerage session needs a few seconds before
            //       /iserver endpoints answer reliably (IBKR-specified). ──
            await Task.Delay(TimeSpan.FromSeconds(4), ct);

            // ── 6. iserver/accounts (confirms /iserver is live) ──
            using (var accReq = BuildAuthed(HttpMethod.Get, "v1/api/iserver/accounts"))
            using (var accResp = await _http.SendAsync(accReq, ct))
            {
                if (!accResp.IsSuccessStatusCode)
                {
                    var accText = await accResp.Content.ReadAsStringAsync(ct);
                    _session.RecordFailure();
                    throw new InvalidOperationException(
                        $"IBKR /iserver/accounts failed {(int)accResp.StatusCode}: {accText}");
                }
            }

            // ── 7. SELECT the account for this brokerage session ──
            //
            // POST /iserver/account {acctId}. Without it the session has no
            // ACTIVE account, and the two /iserver reads that do not name one
            // in their path -- /iserver/account/orders and
            // /iserver/account/trades -- answer for nothing and return [].
            //
            // That is precisely the split observed on 27 Aug 2026. Placement
            // (POST /iserver/account/{accountId}/orders) and positions
            // (GET /portfolio/{accountId}/positions/0) name the account in the
            // URL, and both worked -- a probe BUY came back with broker order
            // id 1069512750 and the paper account's KO holding went 1 -> 2, so
            // the order genuinely executed. The blotter and executions reads
            // are the only two that rely on the session's implicit selection,
            // and they are the only two that returned zero rows. The OMS could
            // therefore never confirm a fill: 57 orders, six recorded at price
            // ZERO, nine stuck in SUBMITTED since July.
            //
            // Not fatal. A failure here leaves the account unselected, which is
            // the state we have been in all along -- placement and positions
            // keep working. But it is recorded and surfaced so it can never
            // again look like an empty blotter, which reads as "no orders"
            // rather than "wrong question".
            if (!string.IsNullOrWhiteSpace(_options.AccountId))
            {
                try
                {
                    // BuildAuthed (not SendWithAuthAsync) — we are INSIDE session
                    // establishment and holding _session.Lock; the auth-ensuring
                    // wrapper would re-enter it and deadlock.
                    using var selReq = BuildAuthed(HttpMethod.Post, "v1/api/iserver/account");
                    selReq.Content = new StringContent(
                        System.Text.Json.JsonSerializer.Serialize(
                            new { acctId = _options.AccountId }),
                        System.Text.Encoding.UTF8, "application/json");
                    using var selResp = await _http.SendAsync(selReq, ct);
                    var selText = await selResp.Content.ReadAsStringAsync(ct);
                    LastAccountSelectOk = selResp.IsSuccessStatusCode;
                    LastAccountSelectRaw = selText.Length > 300 ? selText[..300] : selText;
                    if (selResp.IsSuccessStatusCode)
                        _log.LogInformation(
                            "IBKR brokerage session bound to account {Acct}: {Body}",
                            _options.AccountId, LastAccountSelectRaw);
                    else
                        _log.LogError(
                            "IBKR account selection FAILED {Code} for {Acct}: {Body}. "
                            + "/iserver/account/orders and /trades will return EMPTY, so the "
                            + "OMS cannot confirm any fill.",
                            (int)selResp.StatusCode, _options.AccountId, LastAccountSelectRaw);
                }
                catch (Exception ex)
                {
                    LastAccountSelectOk = false;
                    LastAccountSelectRaw = ex.Message;
                    _log.LogError(ex,
                        "IBKR account selection threw for {Acct} — order/execution reads "
                        + "will return EMPTY", _options.AccountId);
                }
            }

            _session.MarkIserverReady();
            _log.LogInformation("IBKR session established ({Label}, account {Acct}, accountSelected={Sel})",
                _options.BrokerLabel, _options.AccountId, LastAccountSelectOk);
        }
        finally
        {
            _session.Lock.Release();
        }
    }

    /// <summary>Lazy keepalive — tickle only when the 60-90s window has
    /// elapsed. Cheap + idempotent; cheaper than a background timer and
    /// avoids re-auth. Failure here is non-fatal (the next real call's
    /// 401-retry re-establishes the session).</summary>
    private async Task KeepAliveAsync(CancellationToken ct)
    {
        if (!_session.NeedsTickle) return;
        try { await TickleAsync(ct); }
        catch (Exception ex) { _log.LogDebug(ex, "IBKR tickle keepalive failed — will re-auth on next 401"); }
    }

    /// <summary>POST /v1/api/tickle — confirms the session + refreshes the
    /// keepalive clock.</summary>
    private async Task TickleAsync(CancellationToken ct)
    {
        using var req = BuildAuthed(HttpMethod.Post, "v1/api/tickle");
        using var resp = await _http.SendAsync(req, ct);
        if (resp.IsSuccessStatusCode)
        {
            var text = await resp.Content.ReadAsStringAsync(ct);
            try
            {
                var tickle = IBKRResponseParser.ParseTickle(text);
                if (tickle.Session is not null) _session.SetSession(tickle.Session);
            }
            catch { /* tolerant — tickle body shape varies; clock still resets */ }
            _session.MarkTickled();
        }
        else if (resp.StatusCode == HttpStatusCode.Unauthorized)
        {
            _session.Clear();
        }
    }

    /// <summary>POST /v1/api/logout — closes the brokerage session.</summary>
    public async Task LogoutAsync(CancellationToken ct = default)
    {
        if (!_options.IsEnabled || _session.AccessToken is null) return;
        try
        {
            using var req = BuildAuthed(HttpMethod.Post, "v1/api/logout");
            using var _ = await _http.SendAsync(req, ct);
        }
        catch (Exception ex) { _log.LogDebug(ex, "IBKR logout failed (ignored)"); }
        finally { _session.Clear(); }
    }

    /// <summary>IBKR answers the first /iserver blotter read with an empty,
    /// UNPRIMED snapshot and expects the caller to ask again.</summary>
    private const int SnapshotPrimeAttempts = 4;
    private static readonly TimeSpan SnapshotPrimeDelay = TimeSpan.FromMilliseconds(600);

    /// <summary>True when the payload declares itself a REAL snapshot
    /// ("snapshot":true). Absent flag counts as primed — only an explicit
    /// false means "ask again".</summary>
    private static bool IsPrimedSnapshot(string text)
    {
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(text);
            if (doc.RootElement.ValueKind != System.Text.Json.JsonValueKind.Object) return true;
            if (!doc.RootElement.TryGetProperty("snapshot", out var snap)) return true;
            return snap.ValueKind != System.Text.Json.JsonValueKind.False;
        }
        catch { return true; }
    }

    private HttpRequestMessage BuildAuthed(HttpMethod method, string path)
    {
        var req = new HttpRequestMessage(method, path);
        if (_session.AccessToken is not null)
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _session.AccessToken);
        return req;
    }

    /// <summary>Send an authed cpapi request, ensuring the session first;
    /// on 401 clear + re-auth once + retry. Caller owns disposing the
    /// returned response.</summary>
    private async Task<HttpResponseMessage> SendWithAuthAsync(
        HttpMethod method, string path, object? jsonBody, CancellationToken ct)
    {
        await EnsureSessionAsync(ct);

        async Task<HttpResponseMessage> SendOnce()
        {
            using var req = BuildAuthed(method, path);
            if (jsonBody is not null) req.Content = JsonContent.Create(jsonBody);
            return await _http.SendAsync(req, ct);
        }

        var resp = await SendOnce();
        if (resp.StatusCode == HttpStatusCode.Unauthorized)
        {
            _session.Clear();
            resp.Dispose();
            await EnsureSessionAsync(ct);
            resp = await SendOnce();
        }
        return resp;
    }

    // ─── Connectivity check (read-only) ─────────────────────────────

    /// <summary>
    /// Read-only connectivity probe: runs the OAuth bring-up (token →
    /// sso-sessions → tickle → ssodh/init → iserver/accounts) IF a session
    /// isn't already cached, then returns the broker accounts visible to the
    /// authenticated session. Places NO order. Reuses the cached session —
    /// does NOT re-auth per call (IBKR rate-limits that). When disabled it's
    /// a no-op that reports <c>Enabled=false</c> so the operator can confirm
    /// the dormant guard. On failure the IBKR error body is surfaced verbatim
    /// in <see cref="IBKRStatusResult.Error"/> for IP / credential debugging.
    /// </summary>
    public async Task<IBKRStatusResult> GetStatusAsync(CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKRStatusResult(
                Enabled: false, Authenticated: false,
                Accounts: Array.Empty<string>(), AccountIdInUse: null,
                Mode: _options.Mode, BrokerLabel: _options.BrokerLabel,
                IpInUse: null, IpSource: null,
                Error: null);
        try
        {
            // Establishes (or reuses) the session; throws on auth failure
            // carrying the IBKR status code + body in the message.
            await EnsureSessionAsync(ct);

            using var resp = await SendWithAuthAsync(
                HttpMethod.Get, "v1/api/iserver/accounts", null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKRStatusResult(
                    Enabled: true, Authenticated: false,
                    Accounts: Array.Empty<string>(), AccountIdInUse: _options.AccountId,
                    Mode: _options.Mode, BrokerLabel: _options.BrokerLabel,
                    IpInUse: LastResolvedIp, IpSource: LastResolvedIpSource,
                    Error: $"iserver/accounts {(int)resp.StatusCode}: {text}");

            var accounts = IBKRResponseParser.ParseAccounts(text);
            return new IBKRStatusResult(
                Enabled: true, Authenticated: true,
                Accounts: accounts, AccountIdInUse: _options.AccountId,
                Mode: _options.Mode, BrokerLabel: _options.BrokerLabel,
                IpInUse: LastResolvedIp, IpSource: LastResolvedIpSource,
                Error: null);
        }
        catch (Exception ex)
        {
            return new IBKRStatusResult(
                Enabled: true, Authenticated: false,
                Accounts: Array.Empty<string>(), AccountIdInUse: _options.AccountId,
                Mode: _options.Mode, BrokerLabel: _options.BrokerLabel,
                IpInUse: LastResolvedIp, IpSource: LastResolvedIpSource,
                Error: ex.Message);
        }
    }

    // ─── Positions ──────────────────────────────────────────────────

    /// <summary>GET /portfolio/{accountId}/positions/0 — first page of the
    /// configured account's positions, mapped to the neutral IBKR shape.
    ///
    /// IBKR SERVES THIS FROM A CACHE. Discovered 31 Aug 2026: three short puts
    /// were bought back, all three orders returned Filled with remainingQty 0,
    /// and this endpoint kept reporting them OPEN — with byte-identical
    /// unrealised P&L — across repeated reads over several minutes. Nothing
    /// here caches; the staleness is IBKR's own, and it clears only when
    /// /positions/invalidate is called.
    ///
    /// So "did it close?" was a question this platform could not answer, which
    /// is the same fill-blindness that has cost this project whole sessions.
    /// Worse, the flatten sweep VERIFIES against these positions before buying
    /// — a stale read there means refusing a real close, or worse, believing a
    /// position exists that does not.
    ///
    /// Pass forceFresh after anything that MUTATES the book. It costs one extra
    /// round trip, which is far cheaper than a wrong answer about what is held.
    /// </summary>
    public async Task<IBKRPositionsResult> GetPositionsAsync(
        CancellationToken ct = default, bool forceFresh = false)
    {
        if (!_options.IsEnabled)
            return new IBKRPositionsResult(Array.Empty<IBKRPosition>(), "IBKR disabled", 0);
        try
        {
            if (forceFresh)
            {
                // Best-effort: if the invalidate fails we still read, but the
                // read is then possibly stale — which the caller is told about
                // rather than left to assume freshness it did not get.
                try
                {
                    using var inv = await SendWithAuthAsync(
                        HttpMethod.Post,
                        $"v1/api/portfolio/{_options.AccountId}/positions/invalidate", null, ct);
                    if (!inv.IsSuccessStatusCode)
                        _log.LogWarning(
                            "IBKR positions/invalidate returned {Status} — the positions "
                            + "read that follows may be STALE", (int)inv.StatusCode);
                }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "IBKR positions/invalidate failed — read may be STALE");
                }
            }

            using var resp = await SendWithAuthAsync(
                HttpMethod.Get, $"v1/api/portfolio/{_options.AccountId}/positions/0", null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKRPositionsResult(Array.Empty<IBKRPosition>(), text, (int)resp.StatusCode);
            return new IBKRPositionsResult(
                IBKRResponseParser.ParsePositions(text), null, (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IBKRPositionsResult(Array.Empty<IBKRPosition>(), ex.Message, 0);
        }
    }

    // ─── Cash / account summary ─────────────────────────────────────

    /// <summary>GET /portfolio/{accountId}/ledger — cash + net-liquidation
    /// for sizing decisions, mapped to the neutral shape.</summary>
    public async Task<IBKRCashResult> GetCashAsync(CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKRCashResult(null, null, null, null, "IBKR disabled", 0);
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get, $"v1/api/portfolio/{_options.AccountId}/ledger", null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKRCashResult(null, null, null, null, text, (int)resp.StatusCode);
            var s = IBKRResponseParser.ParseLedger(text);
            return new IBKRCashResult(
                s.Cash, s.NetLiquidation, s.UnrealizedPnl, s.Currency, null, (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IBKRCashResult(null, null, null, null, ex.Message, 0);
        }
    }

    // ─── Market data (historical bars, read-only) ───────────────────

    /// <summary>
    /// Historical OHLCV bars for a stock symbol via the IBKR Web API — the SINGLE
    /// IBKR data path (central handler; the Python bar_cache provider consumes this
    /// over HTTP, it does NOT re-implement OAuth). Resolves symbol → conid via
    /// secdef/search, then GET /iserver/marketdata/history.
    ///
    /// <paramref name="period"/> = IBKR window ("1m","6m","1y","2y"…);
    /// <paramref name="bar"/> = bar size ("1d","1h","1w"…).
    ///
    /// FAIL-LOUD: on disabled / no-contract / non-2xx / empty, returns Bars empty
    /// with <c>Error</c> set to the reason — the caller MUST surface it (flag the
    /// symbol in the cockpit); an empty result is NEVER a silent "no data".
    /// </summary>
    public async Task<IBKRHistoryResult> GetPriceHistoryAsync(
        string symbol, string period, string bar, CancellationToken ct = default,
        string? startTime = null)
    {
        if (!_options.IsEnabled)
            return new IBKRHistoryResult(Array.Empty<IBKRBar>(), null, "IBKR disabled", 0);
        var sym = (symbol ?? string.Empty).Trim().ToUpperInvariant();
        if (sym.Length == 0)
            return new IBKRHistoryResult(Array.Empty<IBKRBar>(), null, "empty symbol", 0);
        try
        {
            // 1) symbol → conid (cached; secdef/search best-first STK on miss).
            long conid;
            if (_conidCache.TryGetValue($"{sym}|STK", out var cached))
            {
                conid = cached;
            }
            else using (var searchResp = await SendWithAuthAsync(
                HttpMethod.Get,
                $"v1/api/iserver/secdef/search?symbol={Uri.EscapeDataString(SearchSymbol(sym, "STK"))}&secType=STK",
                null, ct))
            {
                var searchText = await searchResp.Content.ReadAsStringAsync(ct);
                if (!searchResp.IsSuccessStatusCode)
                    return new IBKRHistoryResult(Array.Empty<IBKRBar>(), null,
                        $"conid search failed for {sym}: {searchText}", (int)searchResp.StatusCode);
                var resolved = IBKRResponseParser.ParseConidSearch(searchText);
                if (resolved is null)
                    return new IBKRHistoryResult(Array.Empty<IBKRBar>(), null,
                        $"no IBKR contract for {sym}", (int)searchResp.StatusCode);
                conid = resolved.Value;
                _conidCache[$"{sym}|STK"] = conid;
            }

            // 2) conid → OHLCV history. startTime (YYYYMMDD-HH:mm:ss) anchors the
            //    MOST-RECENT point; IBKR returns `period` worth of bars BACKWARD
            //    from it — the pagination lever for deep history (default = now).
            //
            //    BOUNDED RETRY on transient signatures ("Chart data unavailable",
            //    HMDS 503/5xx/429): the failed request itself warms IBKR's chart
            //    cache, so a short-backoff re-request usually succeeds (verified
            //    live 2026-08-09 — a 0/20 burst, immediate retry succeeded).
            //    Without this, every transient sent bar_cache consumers to the
            //    yfinance/BRONZE fallback. Elapsed-time cap keeps the worst case
            //    (IBKR truly down, ~10s per failed attempt) inside the Python
            //    provider's HTTP timeout instead of retrying forever.
            var startParam = string.IsNullOrWhiteSpace(startTime)
                ? string.Empty
                : $"&startTime={Uri.EscapeDataString(startTime)}";
            var histPath =
                $"v1/api/iserver/marketdata/history?conid={conid}&period={Uri.EscapeDataString(period)}"
                + $"&bar={Uri.EscapeDataString(bar)}&outsideRth=false{startParam}";

            const int maxAttempts = 3;
            var retryBudget = TimeSpan.FromSeconds(25);
            var sw = System.Diagnostics.Stopwatch.StartNew();
            string histText;
            int histStatus;
            var attempt = 0;
            while (true)
            {
                attempt++;
                using var histResp = await SendWithAuthAsync(HttpMethod.Get, histPath, null, ct);
                histText = await histResp.Content.ReadAsStringAsync(ct);
                histStatus = (int)histResp.StatusCode;
                if (histResp.IsSuccessStatusCode) break;
                var transient = IBKRResponseParser.IsTransientHistoryError(histStatus, histText);
                if (!transient || attempt >= maxAttempts || sw.Elapsed > retryBudget)
                    return new IBKRHistoryResult(Array.Empty<IBKRBar>(), conid,
                        $"history fetch failed for {sym} (conid {conid}, attempt {attempt}/{maxAttempts}): {histText}",
                        histStatus);
                _log.LogInformation(
                    "IBKR history transient for {Symbol} (conid {Conid}, attempt {Attempt}/{Max}, HTTP {Status}) — retrying: {Body}",
                    sym, conid, attempt, maxAttempts, histStatus, Truncate(histText, 160));
                await Task.Delay(TimeSpan.FromSeconds(attempt), ct);
            }
            var bars = IBKRResponseParser.ParseHistory(histText);
            if (bars.Count == 0)
                return new IBKRHistoryResult(Array.Empty<IBKRBar>(), conid,
                    $"IBKR returned NO bars for {sym} (conid {conid}, period {period}, bar {bar})",
                    histStatus);
            return new IBKRHistoryResult(bars, conid, null, histStatus);
        }
        catch (Exception ex)
        {
            return new IBKRHistoryResult(Array.Empty<IBKRBar>(), null, ex.Message, 0);
        }
    }

    // ─── Market snapshot (read-only) ────────────────────────────────

    /// <summary>
    /// Fetch a live market-data snapshot for one or more conids via
    /// GET /iserver/marketdata/snapshot?conids=…&amp;fields=…
    ///
    /// Returns a raw JSON string (the first element of the IBKR array) for
    /// the requested conid.  Field numeric codes used by the screener:
    ///   31=last, 7293=52w-high, 7294=52w-low, 7282=iv-pct-52w,
    ///   7283=implied-vol-annual, 7631=hist-vol-30d, 7286=div-yield,
    ///   7282=iv-pct, 87=avg-volume (usd ~90d), 7718=avg-opt-vol.
    /// IBKR may return empty/null for fields that aren't yet "warm" — the
    /// caller should use defaults for missing fields.
    /// </summary>
    /// <summary>
    /// Batched raw snapshot — same endpoint, MANY conids in one request
    /// (IBKR documents a 50-conid cap; callers stay under it). Returns the
    /// raw JSON ARRAY string: one element per conid, each carrying a
    /// "conid" property for correlation. Added 22 Aug 2026: /api/screener/
    /// live burned 30 single-conid calls where one sufficed.
    /// </summary>
    public async Task<string?> GetSnapshotRawBatchAsync(
        IReadOnlyList<long> conids, string fields, CancellationToken ct = default)
    {
        if (!_options.IsEnabled || conids.Count == 0) return null;
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get,
                $"v1/api/iserver/marketdata/snapshot?conids={string.Join(",", conids)}&fields={Uri.EscapeDataString(fields)}",
                null, ct);
            if (!resp.IsSuccessStatusCode) return null;
            return await resp.Content.ReadAsStringAsync(ct);
        }
        catch { return null; }
    }

    public async Task<string?> GetSnapshotRawAsync(
        long conid, string fields, CancellationToken ct = default)
    {
        if (!_options.IsEnabled) return null;
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get,
                $"v1/api/iserver/marketdata/snapshot?conids={conid}&fields={Uri.EscapeDataString(fields)}",
                null, ct);
            if (!resp.IsSuccessStatusCode) return null;
            return await resp.Content.ReadAsStringAsync(ct);
        }
        catch { return null; }
    }

    /// <summary>
    /// RAW body of one of the two fill-visibility reads, for diagnosis only.
    /// Allowlisted paths, GET only, no mutation.
    ///
    /// Exists because three separate investigations this month have stalled at
    /// the same wall: the parsed result is empty and there is no way to tell
    /// whether IBKR returned an empty array, returned a shape the parser drops,
    /// or was answering for the wrong account. Row counts cannot distinguish
    /// those, and each has a completely different fix.
    /// </summary>
    public async Task<(int HttpStatus, string? Body, string? Error)> GetFillReadRawAsync(
        string which, CancellationToken ct = default)
    {
        var path = which switch
        {
            "orders" => "v1/api/iserver/account/orders",
            "trades" => "v1/api/iserver/account/trades",
            "accounts" => "v1/api/iserver/accounts",
            _ => null,
        };
        if (path is null) return (0, null, $"unknown read '{which}'");
        if (!_options.IsEnabled) return (0, null, "IBKR disabled");
        try
        {
            using var resp = await SendWithAuthAsync(HttpMethod.Get, path, null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            return ((int)resp.StatusCode, text.Length > 4000 ? text[..4000] : text, null);
        }
        catch (Exception ex) { return (0, null, ex.Message); }
    }

    /// <summary>Bind this brokerage session to our account on demand —
    /// POST /iserver/account {acctId}. Session establishment does this too, but
    /// a session cached from before that code existed never ran it, and the
    /// cache outlives a deploy only in the sense that establishment is skipped
    /// while it is still valid. Callable so the binding can be applied and
    /// VERIFIED without waiting for a token to age out.</summary>
    public async Task<(int HttpStatus, string? Body, string? Error)> SelectAccountAsync(
        CancellationToken ct = default)
    {
        if (!_options.IsEnabled) return (0, null, "IBKR disabled");
        if (string.IsNullOrWhiteSpace(_options.AccountId)) return (0, null, "no account id configured");
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Post, "v1/api/iserver/account",
                new { acctId = _options.AccountId }, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            LastAccountSelectOk = resp.IsSuccessStatusCode;
            LastAccountSelectRaw = text.Length > 300 ? text[..300] : text;
            _log.LogInformation("IBKR account bind (on demand) {Code}: {Body}",
                (int)resp.StatusCode, LastAccountSelectRaw);
            return ((int)resp.StatusCode, text.Length > 1000 ? text[..1000] : text, null);
        }
        catch (Exception ex)
        {
            LastAccountSelectOk = false;
            LastAccountSelectRaw = ex.Message;
            return (0, null, ex.Message);
        }
    }

    // ─── Option chain (read-only, G3 — TRADEPRO_SPEC_V2.md) ─────────
    //
    // IBKR's documented 3-call option-chain flow: secdef/search (underlying
    // conid + expiration months) -> secdef/strikes (strikes for one month)
    // -> secdef/info (conid per strike/right for one month), then a 4th call
    // -- marketdata/snapshot -- for live bid/ask/greeks/OI on the resolved
    // conids. Same OAuth/session plumbing as every other method on this
    // client (SendWithAuthAsync); no separate auth path.

    /// <summary>
    /// Step 1: resolve a symbol's underlying conid AND its available option
    /// expiration months in one call — GET /iserver/secdef/search?symbol=X.
    /// </summary>
    public async Task<IBKROptionMonthsResult> GetOptionMonthsAsync(
        string symbol, CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKROptionMonthsResult(null, Array.Empty<string>(), "IBKR disabled", 0);
        var sym = (symbol ?? string.Empty).Trim().ToUpperInvariant();
        if (sym.Length == 0)
            return new IBKROptionMonthsResult(null, Array.Empty<string>(), "empty symbol", 0);
        if (_monthsCache.TryGetValue(sym, out var hit)
            && DateTime.UtcNow - hit.AtUtc < _chainMetaTtl)
            return hit.R;
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get,
                $"v1/api/iserver/secdef/search?symbol={Uri.EscapeDataString(sym)}",
                null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKROptionMonthsResult(null, Array.Empty<string>(),
                    $"secdef/search failed for {sym}: {text}", (int)resp.StatusCode);
            var (conId, months) = IBKRResponseParser.ParseOptionMonths(text);
            if (conId is null)
                return new IBKROptionMonthsResult(null, Array.Empty<string>(),
                    $"no IBKR contract for {sym}", (int)resp.StatusCode);
            if (months.Count == 0)
                return new IBKROptionMonthsResult(conId, Array.Empty<string>(),
                    $"{sym} has no listed option chain", (int)resp.StatusCode);
            var ok = new IBKROptionMonthsResult(conId, months, null, (int)resp.StatusCode);
            _monthsCache[sym] = (DateTime.UtcNow, ok);
            return ok;
        }
        catch (Exception ex)
        {
            return new IBKROptionMonthsResult(null, Array.Empty<string>(), ex.Message, 0);
        }
    }

    /// <summary>
    /// Step 2: strikes available for one underlying + expiration month —
    /// GET /iserver/secdef/strikes?conid=U&amp;sectype=OPT&amp;month=MMMYY.
    /// </summary>
    /// <summary>Flags from the last ssodh/init. `competing` is the one that
    /// matters: IBKR grants ONE market-data session per account, and a 200 from
    /// ssodh/init does not mean this session won it.</summary>
    // NULLABLE ON PURPOSE. An unmeasured flag that reports `false` is a
    // confident wrong answer: it reads as "we lost the competition" when it
    // actually means "ssodh/init has not run since this process started". The
    // whole point of these fields is to remove ambiguity from "auth VALID but
    // snapshot DARK" — reporting a default as a measurement would put it back.
    // null = not yet observed. Check LastAuthStatusAtUtc before believing them.
    public bool? LastAuthenticated { get; private set; }
    public bool? LastCompeting { get; private set; }
    public bool? LastConnected { get; private set; }
    public string? LastAuthStatusRaw { get; private set; }

    /// <summary>Did POST /iserver/account bind this session to our account?
    /// null = not yet attempted. When false, /iserver/account/orders and
    /// /iserver/account/trades answer for no account and return [].</summary>
    /// <summary>The account this client is configured to act on (mode-resolved).</summary>
    public string? ConfiguredAccountId => _options.AccountId;

    public bool? LastAccountSelectOk { get; private set; }
    public string? LastAccountSelectRaw { get; private set; }
    public DateTime? LastAuthStatusAtUtc { get; private set; }

    public async Task<IBKROptionStrikesResult> GetOptionStrikesAsync(
        long underlyingConId, string month, CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKROptionStrikesResult(Array.Empty<decimal>(), Array.Empty<decimal>(), "IBKR disabled", 0);
        var strikesKey = $"{underlyingConId}|{month}";
        if (_strikesCache.TryGetValue(strikesKey, out var hit)
            && DateTime.UtcNow - hit.AtUtc < _chainMetaTtl)
            return hit.R;
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get,
                // exchange=SMART per IBKR's documented flow. Omitting it is why 52 of
                // 56 MRVL SEP26 strikes came back "No Contracts retrieved" while the
                // same 195 put resolves fine — the request was under-specified, not
                // the listing missing.
                $"v1/api/iserver/secdef/strikes?conid={underlyingConId}&exchange=SMART&sectype=OPT&month={Uri.EscapeDataString(month)}",
                null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKROptionStrikesResult(Array.Empty<decimal>(), Array.Empty<decimal>(),
                    $"secdef/strikes failed for conid {underlyingConId} month {month}: {text}", (int)resp.StatusCode);
            var (calls, puts) = IBKRResponseParser.ParseStrikes(text);
            if (calls.Count == 0 && puts.Count == 0)
                return new IBKROptionStrikesResult(calls, puts,
                    $"IBKR returned NO strikes for conid {underlyingConId} month {month}", (int)resp.StatusCode);
            var ok = new IBKROptionStrikesResult(calls, puts, null, (int)resp.StatusCode);
            _strikesCache[strikesKey] = (DateTime.UtcNow, ok);
            return ok;
        }
        catch (Exception ex)
        {
            return new IBKROptionStrikesResult(Array.Empty<decimal>(), Array.Empty<decimal>(), ex.Message, 0);
        }
    }

    /// <summary>
    /// Step 3: resolve the tradeable option CONTRACT (conid) for one underlying +
    /// month + strike + right — GET /iserver/secdef/info?conid=U&amp;sectype=OPT&amp;
    /// month=MMMYY&amp;strike=S&amp;right=C|P. CORRECTED 2 Aug 2026: strike is NOT
    /// optional on this IBKR gateway — omitting it 400s ("strike is required for
    /// warrant and option"), confirmed against the live paper account. One call
    /// per strike (the caller narrows to near-the-money strikes FIRST via
    /// GetOptionStrikesAsync + spot before calling this, to bound IBKR call
    /// volume — the earlier "bulk chain in one call" design was wrong).
    /// </summary>
    /// <summary>symbol + expiry + strike + right -> the option's conid.
    ///
    /// Composes the pieces that already existed (ResolveConidAsync for the
    /// underlying, GetOptionContractsAsync for the contract) rather than adding
    /// a parallel resolution path — there is already one place where option
    /// contracts are resolved and it should stay that way.
    ///
    /// Added 31 Aug 2026 for paper strangle execution: PlaceMarketOrderAsync
    /// takes a conid, and PlaceMarketOrderBySymbolAsync hardcodes "STK", so an
    /// option had no way through. Returns null on ANY failure — the caller must
    /// treat that as "do not place", never as "place something close".
    /// </summary>
    public async Task<long?> ResolveOptionConidAsync(
        string symbol, string expiry, decimal strike, string right,
        CancellationToken ct = default)
    {
        if (!_options.IsEnabled || string.IsNullOrWhiteSpace(symbol)) return null;
        if (!DateTime.TryParse(expiry, out var exp)) return null;
        var underlying = await ResolveConidAsync(symbol, "STK", ct, useCache: true);
        if (underlying is null) return null;
        // IBKR months are e.g. OCT26.
        var month = exp.ToString("MMM", System.Globalization.CultureInfo.InvariantCulture)
                        .ToUpperInvariant() + exp.ToString("yy");
        var res = await GetOptionContractsAsync(underlying.Value, month, strike,
                                                right.Trim().ToUpperInvariant(), ct);
        if (res.Contracts.Count == 0) return null;
        // Match the EXACT expiry when the contract carries one — a month can
        // hold several expiries (weeklies), and placing the wrong one is a
        // different trade with the same strike.
        var want = exp.ToString("yyyyMMdd");
        var exact = res.Contracts.FirstOrDefault(
            c => c.MaturityDate is not null && c.MaturityDate.Replace("-", "") == want);
        if (exact is not null) return exact.ConId;
        // No maturity on the rows: only safe if the month holds exactly one.
        return res.Contracts.Count == 1 ? res.Contracts[0].ConId : null;
    }

    public async Task<IBKROptionContractsResult> GetOptionContractsAsync(
        long underlyingConId, string month, decimal strike, string right, CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKROptionContractsResult(Array.Empty<IBKROptionContract>(), "IBKR disabled", 0);
        var contractKey = FormattableString.Invariant(
            $"{underlyingConId}|{month}|{strike}|{right}");
        if (_optContractCache.TryGetValue(contractKey, out var cachedContracts))
            return cachedContracts;
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Get,
                // exchange=SMART — IBKR's own example carries it on BOTH secdef calls:
                //   secdef/info?conid=265598&exchange=SMART&sectype=OPT&month=OCT24&strike=217.5
                // We sent neither, and 52 of 56 strikes answered "No Contracts
                // retrieved" — read for weeks as "IBKR does not list that strike",
                // when the same contract resolves through a fully-specified request.
                //
                // The month IS correct, contrary to what I assumed before reading the
                // doc: secdef/info takes a MONTH and returns every expiration inside
                // it (IBKR's example returns four records for OCT24 — the 18th and the
                // 25th, call and put). So the narrow chain was never an expiry problem.
                $"v1/api/iserver/secdef/info?conid={underlyingConId}&exchange=SMART&sectype=OPT&month={Uri.EscapeDataString(month)}"
                + $"&strike={Uri.EscapeDataString(strike.ToString(System.Globalization.CultureInfo.InvariantCulture))}"
                + $"&right={Uri.EscapeDataString(right)}",
                null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKROptionContractsResult(Array.Empty<IBKROptionContract>(),
                    $"secdef/info failed for conid {underlyingConId} month {month} strike {strike} right {right}: {text}",
                    (int)resp.StatusCode);
            var contracts = IBKRResponseParser.ParseOptionContracts(text);
            if (contracts.Count == 0)
                return new IBKROptionContractsResult(contracts,
                    $"IBKR returned NO contract for conid {underlyingConId} month {month} strike {strike} right {right}",
                    (int)resp.StatusCode);
            var ok = new IBKROptionContractsResult(contracts, null, (int)resp.StatusCode);
            _optContractCache[contractKey] = ok;
            return ok;
        }
        catch (Exception ex)
        {
            return new IBKROptionContractsResult(Array.Empty<IBKROptionContract>(), ex.Message, 0);
        }
    }

    /// <summary>
    /// Step 4: batched live quote — GET /iserver/marketdata/snapshot?conids=...&amp;fields=...
    /// for bid/ask/last/greeks/OI on option conids, chunked to IBKR's documented 50-conid
    /// cap per call. Field codes are IBKR's documented cpapi numbering, cross-checked
    /// against the Voyz/ibind open-source client (IBKR's own field-reference page
    /// returned HTTP 403 from this environment, so it could not be read directly) —
    /// treat these as NEEDING LIVE VERIFICATION against the paper account (e.g. a known
    /// SPY strike) before the wheel loop depends on them: 31 last, 84 bid, 86 ask,
    /// 88 bid size, 85 ask size, 87 volume, 7308 delta, 7309 gamma, 7310 theta,
    /// 7311 vega, 7633 IV%, 7638 open interest.
    /// </summary>
    public async Task<IBKROptionQuotesResult> GetOptionSnapshotBatchAsync(
        IReadOnlyList<long> conids, CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKROptionQuotesResult(Array.Empty<IBKROptionQuote>(), "IBKR disabled", 0);
        if (conids.Count == 0)
            return new IBKROptionQuotesResult(Array.Empty<IBKROptionQuote>(), null, 0);
        const int ChunkSize = 50;
        const string Fields = "31,84,86,88,85,87,7308,7309,7310,7311,7633,7638,7741";
        var all = new List<IBKROptionQuote>();
        var lastStatus = 0;
        for (int i = 0; i < conids.Count; i += ChunkSize)
        {
            var chunk = conids.Skip(i).Take(ChunkSize).ToArray();
            var conidsParam = string.Join(",", chunk);
            try
            {
                // /iserver/marketdata/snapshot SUBSCRIBES. The first request
                // registers the conids and answers empty or partial; the data
                // arrives on a LATER call. We asked once and read the empty
                // first answer as "no option data on this account".
                //
                // That single missing retry is why the wheel screen has run on
                // Yahoo open interest for weeks while IBKR held the real
                // numbers: the same MRVL Sep-18 195 put that returns null here
                // returns OI 2,472, IV 57.2% and bid 3.30/ask 3.70 through the
                // live session. Sharing was ON the whole time, the session was
                // healthy the whole time (the underlying's SPOT came back at
                // 216.39 in the same call that gave null greeks), and three
                // separate theories -- no OPRA entitlement, session contention,
                // wrong field codes -- were all wrong.
                //
                // Identical shape to /iserver/account/orders answering
                // {"orders":[],"snapshot":false} on its first call, which hid
                // every fill for two months (e1ed6e4). Second time in this file.
                var text = string.Empty;
                var bestFilled = -1;
                IReadOnlyList<IBKROptionQuote>? best = null;
                for (var attempt = 1; attempt <= SnapshotPrimeAttempts; attempt++)
                {
                    using var resp = await SendWithAuthAsync(
                        HttpMethod.Get,
                        $"v1/api/iserver/marketdata/snapshot?conids={conidsParam}&fields={Fields}",
                        null, ct);
                    text = await resp.Content.ReadAsStringAsync(ct);
                    lastStatus = (int)resp.StatusCode;
                    if (!resp.IsSuccessStatusCode)
                        return new IBKROptionQuotesResult(all,
                            $"marketdata/snapshot failed for {chunk.Length} conids: {text}", lastStatus);
                    // Poll until the answer STOPS IMPROVING, not until the first
                    // field lands. Fields prime at different rates: measured
                    // 30 Aug on a paper session, bid/ask arrived on call 2 while
                    // OI and IV had still not appeared by call 4. Breaking on
                    // "any field present" would take the bid/ask answer and
                    // abandon open interest — which is the ONE field the wheel's
                    // liquidity gate actually rejects on.
                    //
                    // Judged on PARSED FIELDS, never on row count: IBKR returns a
                    // row per conid immediately carrying only the conid, so
                    // counting rows would call an unprimed answer a success.
                    var parsed = IBKRResponseParser.ParseOptionSnapshot(text);
                    var filled = parsed.Count(q => q.Bid is not null) + parsed.Count(q => q.Ask is not null)
                               + parsed.Count(q => q.OpenInterest is not null)
                               + parsed.Count(q => q.ImpliedVolPct is not null);
                    if (filled > bestFilled) { bestFilled = filled; best = parsed; }
                    if (attempt == SnapshotPrimeAttempts || (filled > 0 && filled == bestFilled && attempt > 1))
                    {
                        all.AddRange(best ?? parsed);
                        // Name the fields that never arrived. A silently partial
                        // chain is how "IBKR has no open interest" became folklore
                        // for weeks when the truth was that we stopped asking.
                        var res = best ?? parsed;
                        if (res.Count > 0 && res.All(q => q.OpenInterest is null))
                            _log.LogWarning(
                                "IBKR option snapshot: {N} legs primed but OPEN INTEREST never arrived "
                                + "after {A} calls (bid/ask on {B}) — field 7638 may be wrong, or OI is "
                                + "not served on this session. NOT the same as 'no open interest'.",
                                res.Count, attempt, res.Count(q => q.Bid is not null));
                        break;
                    }
                    await Task.Delay(SnapshotPrimeDelay, ct);
                }
            }
            catch (Exception ex)
            {
                return new IBKROptionQuotesResult(all, ex.Message, lastStatus);
            }
        }
        return new IBKROptionQuotesResult(all, null, lastStatus);
    }

    // ─── Orders ─────────────────────────────────────────────────────

    /// <summary>
    /// Place a MARKET order on the configured account via
    /// POST /iserver/account/{accountId}/orders.
    ///
    /// <paramref name="conid"/> is IBKR's numeric contract id (the
    /// broker-native instrument key — resolved upstream from
    /// broker_ticker_map, NOT guessed here). <paramref name="side"/> =
    /// "BUY" / "SELL"; <paramref name="quantity"/> is the absolute share
    /// count. Returns an ACCEPTED ack with the order id, or NEEDS_CONFIRM
    /// when IBKR requires a /iserver/reply confirmation (we surface the
    /// reply id rather than silently auto-confirming a margin/price warning).
    /// </summary>
    public async Task<IBKROrderResult> PlaceMarketOrderAsync(
        long conid, string side, decimal quantity, CancellationToken ct = default)
    {
        // ── HARD kill-switch (Layer 1 — primary guarantee) ──
        // We run IBKR READ-ONLY against a LIVE account. Unless an operator has
        // EXPLICITLY set IBKR:AllowOrders=true (which we will NOT set; it is
        // absent from the secret and so binds to false), NO order may ever hit
        // IBKR. Return a rejected result immediately, BEFORE building or sending
        // ANY HTTP request — even a direct caller cannot place an order.
        if (!_options.AllowOrders)
        {
            _log.LogWarning(
                "IBKR order BLOCKED by kill-switch (read-only mode): conid={Conid} side={Side} qty={Qty}. "
                + "Set IBKR:AllowOrders=true to enable.",
                conid, side, quantity);
            return new IBKROrderResult(
                null, "REJECTED",
                "IBKR order placement is disabled (read-only mode) — set IBKR:AllowOrders=true to enable",
                0);
        }
        if (!_options.IsEnabled)
            return new IBKROrderResult(null, "REJECTED", "IBKR disabled", 0);
        try
        {
            var body = new
            {
                orders = new[]
                {
                    new
                    {
                        conid,
                        orderType = "MKT",
                        side = side.ToUpperInvariant(),
                        quantity = Math.Abs(quantity),
                        tif = "DAY",
                    },
                },
            };
            using var resp = await SendWithAuthAsync(
                HttpMethod.Post, $"v1/api/iserver/account/{_options.AccountId}/orders", body, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKROrderResult(null, "REJECTED", text, (int)resp.StatusCode);

            var ack = IBKRResponseParser.ParseOrderAck(text);
            if (ack.Error is not null)
                return new IBKROrderResult(null, "REJECTED", ack.Error, (int)resp.StatusCode);
            if (ack.OrderId is null && ack.ReplyId is not null)
                return new IBKROrderResult(ack.ReplyId, "NEEDS_CONFIRM",
                    "IBKR requires order confirmation (reply id)", (int)resp.StatusCode);
            return new IBKROrderResult(ack.OrderId, "ACCEPTED", ack.OrderStatus, (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IBKROrderResult(null, "PARSE_ERROR", ex.Message, 0);
        }
    }

    /// <summary>
    /// Confirm an IBKR order-precaution reply — the "are you sure" warning IBKR
    /// returns for most orders (price/size/liquidity). POST /iserver/reply/{id}
    /// {confirmed:true}. IBKR can CHAIN replies, so the caller loops while the
    /// response is another NEEDS_CONFIRM. Behind the SAME kill-switch as
    /// placement (a reply advances a live order), so a read-only deploy can
    /// never confirm one either.
    /// </summary>
    public async Task<IBKROrderResult> ConfirmReplyAsync(string replyId, CancellationToken ct = default)
    {
        if (!_options.AllowOrders)
            return new IBKROrderResult(null, "REJECTED", "IBKR order placement disabled (read-only)", 0);
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Post, $"v1/api/iserver/reply/{Uri.EscapeDataString(replyId)}",
                new { confirmed = true }, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKROrderResult(null, "REJECTED", text, (int)resp.StatusCode);
            var ack = IBKRResponseParser.ParseOrderAck(text);
            if (ack.Error is not null)
                return new IBKROrderResult(null, "REJECTED", ack.Error, (int)resp.StatusCode);
            if (ack.OrderId is null && ack.ReplyId is not null)
                return new IBKROrderResult(ack.ReplyId, "NEEDS_CONFIRM", "chained confirmation", (int)resp.StatusCode);
            return new IBKROrderResult(ack.OrderId, "ACCEPTED", ack.OrderStatus, (int)resp.StatusCode);
        }
        catch (Exception ex) { return new IBKROrderResult(null, "PARSE_ERROR", ex.Message, 0); }
    }

    /// <summary>
    /// Place a market order AND drive the confirmation dance to completion:
    /// place → while NEEDS_CONFIRM, POST the reply (bounded to <c>MaxConfirms</c>)
    /// → return the REAL broker order id. This is the entry the OMS calls so an
    /// order comes back with a broker_order_id instead of stalling at
    /// NEEDS_CONFIRM (the reason the clone's fills carried no broker id).
    ///
    /// Mode is resolved from the secret (paper/live) and LOGGED on every order
    /// so routing is auditable — you can always answer "which account did this
    /// hit?". The kill-switch (<see cref="IBKROptions.AllowOrders"/>) still gates
    /// it, and the account is <see cref="IBKROptions.AccountId"/> (mode-resolved),
    /// so a paper secret can only ever place to the paper account.
    /// </summary>
    public async Task<IBKROrderResult> PlaceMarketOrderConfirmedAsync(
        long conid, string side, decimal quantity, CancellationToken ct = default)
    {
        _log.LogInformation(
            "IBKR order routing: mode={Mode} account={Account} label={Label} conid={Conid} side={Side} qty={Qty}",
            _options.Mode, _options.AccountId, _options.BrokerLabel, conid, side, quantity);

        var result = await PlaceMarketOrderAsync(conid, side, quantity, ct);
        const int MaxConfirms = 5;
        for (int i = 0; i < MaxConfirms && result.Status == "NEEDS_CONFIRM" && result.OrderId is not null; i++)
        {
            _log.LogInformation(
                "IBKR order needs confirmation (reply {ReplyId}) — confirming {N}/{Max}",
                result.OrderId, i + 1, MaxConfirms);
            result = await ConfirmReplyAsync(result.OrderId, ct);
        }
        if (result.Status == "NEEDS_CONFIRM")
            return new IBKROrderResult(null, "REJECTED",
                $"order still needs confirmation after {MaxConfirms} replies", result.HttpStatus);
        return result;
    }

    /// <summary>
    /// Symbol-level convenience: resolve symbol→conid (secdef search) then
    /// place + confirm. The Python desks emit symbols, so this keeps conid
    /// resolution in the one authenticated place. <paramref name="secType"/>
    /// defaults to "STK" (equities); pass "CASH" for FX pairs. Fail-loud: an
    /// unresolvable symbol REJECTS (never a silent no-op that looks placed).
    /// </summary>
    public async Task<IBKROrderResult> PlaceMarketOrderBySymbolAsync(
        string symbol, string side, decimal quantity, string secType = "STK", CancellationToken ct = default)
    {
        if (!_options.AllowOrders)
            return new IBKROrderResult(null, "REJECTED", "IBKR order placement disabled (read-only)", 0);
        // Orders resolve FRESH every time (useCache: false): a stale conid
        // after a corporate action must surface as an IBKR-side reject on a
        // fresh lookup, never as an order routed via a cached mapping.
        var conid = await ResolveConidAsync(symbol, secType, ct, useCache: false);
        if (conid is null)
            return new IBKROrderResult(null, "REJECTED", $"no IBKR contract for {symbol} ({secType})", 0);
        return await PlaceMarketOrderConfirmedAsync(conid.Value, side, quantity, ct);
    }

    /// <summary>symbol → conid via secdef/search (best-first match). Null when
    /// there's no match (caller FLAGS it — fail-loud). Shared helper so orders
    /// and price-history resolve contracts the same way. Cached process-wide
    /// (see <c>_conidCache</c>); pass <paramref name="useCache"/>=false for
    /// paths that must resolve fresh (order placement).</summary>
    public async Task<long?> ResolveConidAsync(
        string symbol, string secType = "STK", CancellationToken ct = default,
        bool useCache = true)
    {
        var sym = (symbol ?? string.Empty).Trim().ToUpperInvariant();
        if (sym.Length == 0) return null;
        var cacheKey = $"{sym}|{secType.Trim().ToUpperInvariant()}";
        if (useCache && _conidCache.TryGetValue(cacheKey, out var cached))
            return cached;
        using var searchResp = await SendWithAuthAsync(
            HttpMethod.Get,
            $"v1/api/iserver/secdef/search?symbol={Uri.EscapeDataString(SearchSymbol(sym, secType))}&secType={Uri.EscapeDataString(secType)}",
            null, ct);
        if (!searchResp.IsSuccessStatusCode) return null;
        var searchText = await searchResp.Content.ReadAsStringAsync(ct);
        var resolved = IBKRResponseParser.ParseConidSearch(searchText);
        if (resolved is not null)
            _conidCache[cacheKey] = resolved.Value;
        return resolved;
    }

    /// <summary>
    /// The broker's live order blotter (GET /iserver/account/orders) — the
    /// GOLDEN SOURCE the OMS reconciles TO. Read-only (no kill-switch): reading
    /// order status never mutates anything.
    /// </summary>
    public async Task<IBKROrdersResult> GetLiveOrdersAsync(CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKROrdersResult(Array.Empty<IBKRLiveOrder>(), "IBKR disabled", 0);
        try
        {
            // IBKR PRIMES this endpoint. The first call after a session becomes
            // ready returns {"orders":[],"snapshot":false} -- an EMPTY, UNPRIMED
            // snapshot -- and the caller must ask again to get the real blotter.
            // The flag is in the payload; we simply never read it.
            //
            // Reconcile called ONCE, every time, and therefore read an empty
            // blotter every time. That is the whole of the "orders read is
            // broken" story: 57 orders, six fills recorded at price ZERO, nine
            // stuck in SUBMITTED since July, and forward-test gates F2/F3/F4
            // uncomputable -- from a documented two-call handshake done once.
            //
            // Proven 27 Aug 2026: call 1 -> {"orders":[],"snapshot":false};
            // call 2 -> orderId 1069512750, status Filled, avgPrice 89.36.
            for (var attempt = 1; ; attempt++)
            {
                using var resp = await SendWithAuthAsync(
                    HttpMethod.Get, "v1/api/iserver/account/orders", null, ct);
                var text = await resp.Content.ReadAsStringAsync(ct);
                if (!resp.IsSuccessStatusCode)
                    return new IBKROrdersResult(
                        Array.Empty<IBKRLiveOrder>(), text, (int)resp.StatusCode);

                var primed = IsPrimedSnapshot(text);
                var parsed = IBKRResponseParser.ParseLiveOrders(text);
                if (primed || parsed.Count > 0 || attempt >= SnapshotPrimeAttempts)
                {
                    if (!primed && parsed.Count == 0)
                        _log.LogWarning(
                            "IBKR order blotter still UNPRIMED after {N} attempts — "
                            + "reporting empty, but this is not evidence of no orders",
                            attempt);
                    return new IBKROrdersResult(parsed, null, (int)resp.StatusCode);
                }
                _log.LogInformation(
                    "IBKR order blotter unprimed (snapshot:false) — re-asking {N}/{Max}",
                    attempt, SnapshotPrimeAttempts);
                await Task.Delay(SnapshotPrimeDelay, ct);
            }
        }
        catch (Exception ex)
        {
            return new IBKROrdersResult(Array.Empty<IBKRLiveOrder>(), ex.Message, 0);
        }
    }

    /// <summary>
    /// Today's EXECUTIONS — GET /iserver/account/trades. Read-only (no order is
    /// placed). Used to record the paper clone's actual fills into the ledger so
    /// fills_count &gt; 0, realised P&amp;L computes, and chart markers appear — the
    /// clone's orders route through an ack-less path that never emits a fill event.
    /// Filtered to this client's account (the endpoint returns all accounts).
    /// </summary>
    public async Task<IBKRTradesResult> GetTradesAsync(CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKRTradesResult(Array.Empty<IBKRTrade>(), "IBKR disabled", 0);
        try
        {
            // Primed the same way as the order blotter, but the executions
            // payload is a BARE ARRAY with no snapshot flag -- so an unprimed
            // read and a genuinely quiet day are both []. Re-ask a bounded
            // number of times and take the first non-empty answer; a real quiet
            // day costs a couple of extra reads and still returns [].
            string text = "[]";
            HttpStatusCode code = HttpStatusCode.OK;
            IReadOnlyList<IBKRTrade> all = Array.Empty<IBKRTrade>();
            for (var attempt = 1; attempt <= SnapshotPrimeAttempts; attempt++)
            {
                using var resp = await SendWithAuthAsync(
                    HttpMethod.Get, "v1/api/iserver/account/trades", null, ct);
                text = await resp.Content.ReadAsStringAsync(ct);
                code = resp.StatusCode;
                if (!resp.IsSuccessStatusCode)
                    return new IBKRTradesResult(Array.Empty<IBKRTrade>(), text, (int)code);
                all = IBKRResponseParser.ParseTrades(text);
                if (all.Count > 0) break;
                if (attempt < SnapshotPrimeAttempts) await Task.Delay(SnapshotPrimeDelay, ct);
            }
            var acct = _options.AccountId;
            var mine = string.IsNullOrWhiteSpace(acct)
                ? all
                : all.Where(t => string.IsNullOrWhiteSpace(t.Account)
                                 || string.Equals(t.Account, acct, StringComparison.OrdinalIgnoreCase)).ToList();
            return new IBKRTradesResult(mine, null, (int)code);
        }
        catch (Exception ex)
        {
            return new IBKRTradesResult(Array.Empty<IBKRTrade>(), ex.Message, 0);
        }
    }

    /// <summary>
    /// Cancel a working order by its broker order id —
    /// DELETE /iserver/account/{accountId}/order/{orderId}. Behind the same
    /// kill-switch as placement (a cancel mutates a live order). Used to pull a
    /// PreSubmitted order before it fills.
    /// </summary>
    public async Task<IBKROrderResult> CancelOrderAsync(string brokerOrderId, CancellationToken ct = default)
    {
        if (!_options.AllowOrders)
            return new IBKROrderResult(brokerOrderId, "REJECTED", "IBKR order actions disabled (read-only)", 0);
        if (!_options.IsEnabled)
            return new IBKROrderResult(brokerOrderId, "REJECTED", "IBKR disabled", 0);
        try
        {
            using var resp = await SendWithAuthAsync(
                HttpMethod.Delete,
                $"v1/api/iserver/account/{_options.AccountId}/order/{Uri.EscapeDataString(brokerOrderId)}",
                null, ct);
            var text = await resp.Content.ReadAsStringAsync(ct);
            if (!resp.IsSuccessStatusCode)
                return new IBKROrderResult(brokerOrderId, "REJECTED", text, (int)resp.StatusCode);
            _log.LogInformation("IBKR order {OrderId} cancel requested on {Account}", brokerOrderId, _options.AccountId);
            return new IBKROrderResult(brokerOrderId, "CANCELLED", text, (int)resp.StatusCode);
        }
        catch (Exception ex) { return new IBKROrderResult(brokerOrderId, "PARSE_ERROR", ex.Message, 0); }
    }

    private static string Truncate(string s, int max)
        => s.Length <= max ? s : s[..max] + "…";
}
