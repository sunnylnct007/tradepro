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
    private readonly HttpClient _http;
    private readonly IBKROptions _options;
    private readonly IBKRSessionCache _session;
    private readonly IBKREgressIpResolver _ipResolver;
    private readonly ILogger<IBKRClient> _log;

    public IBKRClient(
        HttpClient http,
        IOptions<IBKROptions> options,
        IBKRSessionCache session,
        IBKREgressIpResolver ipResolver,
        ILogger<IBKRClient> log)
    {
        _http = http;
        _options = options.Value;
        _session = session;
        _ipResolver = ipResolver;
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
    public bool AllowOrders => _options.AllowOrders;

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
                if (!initResp.IsSuccessStatusCode)
                {
                    var initText = await initResp.Content.ReadAsStringAsync(ct);
                    _session.RecordFailure();
                    throw new InvalidOperationException(
                        $"IBKR /iserver/auth/ssodh/init failed {(int)initResp.StatusCode}: {initText}");
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

            _session.MarkIserverReady();
            _log.LogInformation("IBKR session established ({Label}, account {Acct})",
                _options.BrokerLabel, _options.AccountId);
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
    /// configured account's positions, mapped to the neutral IBKR shape.</summary>
    public async Task<IBKRPositionsResult> GetPositionsAsync(CancellationToken ct = default)
    {
        if (!_options.IsEnabled)
            return new IBKRPositionsResult(Array.Empty<IBKRPosition>(), "IBKR disabled", 0);
        try
        {
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
}
