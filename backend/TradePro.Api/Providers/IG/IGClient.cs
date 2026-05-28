using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.IG;

/// <summary>
/// IG Markets REST API client. Single class because the IG surface we
/// touch is small (session, place order, get order/position, cash) and
/// adding more files would just spread the IG-specific oddities (CST
/// + X-SECURITY-TOKEN headers, EPIC vs ticker translation, expiry
/// codes) across the codebase.
///
/// Session management: re-login on first call OR when CST is unset.
/// The tokens are good for ~6 hours; we don't proactively refresh
/// (would need a background refresher). On HTTP 401 from any call,
/// we re-login once + retry — covers the expiry case.
///
/// Order placement: POST /positions/otc with currencyCode + epic +
/// expiry ("-" for cash equities, "DFB" for daily FX) + direction +
/// size + orderType ("MARKET"). Response includes dealReference; we
/// poll /confirms/{dealReference} for the final status (FILLED /
/// REJECTED / OPEN / DELETED).
/// </summary>
public sealed class IGClient
{
    private readonly HttpClient _http;
    private readonly IGOptions _options;
    private readonly ILogger<IGClient> _log;

    // Session tokens — set after a successful /session call. Both
    // must be present on every subsequent request.
    private string? _cst;
    private string? _xSecurityToken;
    private readonly SemaphoreSlim _loginLock = new(1, 1);

    public IGClient(
        HttpClient http,
        IOptions<IGOptions> options,
        ILogger<IGClient> log)
    {
        _http = http;
        _options = options.Value;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.BaseUrl);
            // IG requires Accept-Version on every call. v1 covers
            // what we need; some calls require v2 — set per-request
            // when needed.
            _http.DefaultRequestHeaders.Accept.Add(
                new MediaTypeWithQualityHeaderValue("application/json"));
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string BrokerLabel => _options.BrokerLabel;

    // ─── Session ────────────────────────────────────────────────

    /// <summary>POST /session to obtain CST + X-SECURITY-TOKEN. Called
    /// on first use and on 401-retry. Thread-safe via _loginLock so
    /// concurrent callers don't all hit /session at once.</summary>
    private async Task LoginAsync(CancellationToken ct)
    {
        await _loginLock.WaitAsync(ct);
        try
        {
            if (_cst is not null && _xSecurityToken is not null) return;
            var body = new
            {
                identifier = _options.Username,
                password = _options.Password,
            };
            using var req = new HttpRequestMessage(HttpMethod.Post, "session")
            {
                Content = JsonContent.Create(body),
            };
            req.Headers.Add("X-IG-API-KEY", _options.ApiKey);
            req.Headers.Add("Version", "2");
            using var resp = await _http.SendAsync(req, ct);
            if (!resp.IsSuccessStatusCode)
            {
                var bodyText = await resp.Content.ReadAsStringAsync(ct);
                throw new InvalidOperationException(
                    $"IG /session failed {(int)resp.StatusCode}: {bodyText}");
            }
            // IG returns the session tokens in RESPONSE headers, not body.
            _cst = resp.Headers.TryGetValues("CST", out var cst)
                ? cst.FirstOrDefault() : null;
            _xSecurityToken = resp.Headers.TryGetValues("X-SECURITY-TOKEN", out var xst)
                ? xst.FirstOrDefault() : null;
            if (_cst is null || _xSecurityToken is null)
            {
                throw new InvalidOperationException(
                    "IG /session returned 200 but no CST / X-SECURITY-TOKEN headers");
            }
            _log.LogInformation("IG session established ({Mode})", _options.Mode);
        }
        finally
        {
            _loginLock.Release();
        }
    }

    private async Task<HttpResponseMessage> SendWithAuthAsync(
        HttpMethod method, string path, object? jsonBody, string version, CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            throw new InvalidOperationException("IG client is disabled — set Mode + creds");
        }
        if (_cst is null) await LoginAsync(ct);

        async Task<HttpResponseMessage> SendOnce()
        {
            using var req = new HttpRequestMessage(method, path);
            req.Headers.Add("X-IG-API-KEY", _options.ApiKey);
            req.Headers.Add("Version", version);
            if (_cst is not null) req.Headers.Add("CST", _cst);
            if (_xSecurityToken is not null) req.Headers.Add("X-SECURITY-TOKEN", _xSecurityToken);
            if (jsonBody is not null) req.Content = JsonContent.Create(jsonBody);
            return await _http.SendAsync(req, ct);
        }

        var resp = await SendOnce();
        if (resp.StatusCode == HttpStatusCode.Unauthorized)
        {
            // Token expired — clear + retry once.
            _cst = null;
            _xSecurityToken = null;
            resp.Dispose();
            await LoginAsync(ct);
            resp = await SendOnce();
        }
        return resp;
    }

    // ─── Orders ─────────────────────────────────────────────────

    /// <summary>Place a market order. <paramref name="epic"/> is the
    /// IG instrument code (e.g. "IX.D.SPTRD.IFE.IP" for SPX); for
    /// equities it's typically "<symbol>.US.EQ" but the mapping is
    /// per-listing. <paramref name="direction"/> = "BUY" / "SELL".
    /// Returns the deal reference; poll <see cref="ConfirmDealAsync"/>
    /// for the final status.</summary>
    public async Task<IGOrderResult> PlaceMarketOrderAsync(
        string epic, string direction, decimal size,
        string? expiry = "-",
        CancellationToken ct = default)
    {
        var body = new
        {
            epic,
            expiry = expiry ?? "-",
            direction = direction.ToUpperInvariant(),  // BUY / SELL
            size,
            orderType = "MARKET",
            currencyCode = "USD",
            forceOpen = true,                          // open new position
            guaranteedStop = false,
        };
        using var resp = await SendWithAuthAsync(
            HttpMethod.Post, "positions/otc", body, version: "2", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGOrderResult(
                DealReference: null,
                Status: "REJECTED",
                StatusReason: text,
                HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            var dealRef = doc.RootElement.TryGetProperty("dealReference", out var dr)
                ? dr.GetString() : null;
            return new IGOrderResult(
                DealReference: dealRef,
                Status: "ACCEPTED",
                StatusReason: null,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGOrderResult(
                DealReference: null,
                Status: "PARSE_ERROR",
                StatusReason: ex.Message,
                HttpStatus: (int)resp.StatusCode);
        }
    }

    /// <summary>Poll /confirms/{dealReference} for the order's final
    /// status. IG returns dealStatus = ACCEPTED / REJECTED and reason
    /// = the rejection cause.</summary>
    public async Task<IGOrderResult> ConfirmDealAsync(string dealReference, CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, $"confirms/{dealReference}", null, version: "1", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGOrderResult(
                DealReference: dealReference,
                Status: "UNKNOWN",
                StatusReason: text,
                HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            var dealStatus = root.TryGetProperty("dealStatus", out var ds) ? ds.GetString() : null;
            var reason = root.TryGetProperty("reason", out var rs) ? rs.GetString() : null;
            return new IGOrderResult(
                DealReference: dealReference,
                Status: dealStatus ?? "UNKNOWN",
                StatusReason: reason,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGOrderResult(
                DealReference: dealReference,
                Status: "PARSE_ERROR",
                StatusReason: ex.Message,
                HttpStatus: (int)resp.StatusCode);
        }
    }

    // ─── Positions ─────────────────────────────────────────────

    /// <summary>GET /positions — all open positions on the account.
    /// Cached at a higher layer (mirrors the T212 positions cache
    /// pattern) so we don't hit IG every UI render.</summary>
    public async Task<IGPositionsResult> GetPositionsAsync(CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, "positions", null, version: "2", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGPositionsResult(
                Positions: Array.Empty<IGPosition>(),
                Error: text, HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            var positions = new List<IGPosition>();
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("positions", out var arr)
                && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in arr.EnumerateArray())
                {
                    var pos = item.TryGetProperty("position", out var p) ? p : default;
                    var market = item.TryGetProperty("market", out var m) ? m : default;
                    var epic = market.ValueKind == JsonValueKind.Object
                               && market.TryGetProperty("epic", out var e)
                        ? e.GetString() : null;
                    var instrumentName = market.ValueKind == JsonValueKind.Object
                                         && market.TryGetProperty("instrumentName", out var inm)
                        ? inm.GetString() : null;
                    var direction = pos.TryGetProperty("direction", out var d) ? d.GetString() : null;
                    var size = pos.TryGetProperty("size", out var sz) ? sz.GetDecimal() : 0m;
                    var level = pos.TryGetProperty("level", out var lv) ? lv.GetDecimal() : 0m;
                    var dealId = pos.TryGetProperty("dealId", out var di) ? di.GetString() : null;
                    positions.Add(new IGPosition(
                        Epic: epic ?? "?",
                        InstrumentName: instrumentName,
                        Direction: direction ?? "?",
                        Size: size,
                        EntryLevel: level,
                        DealId: dealId));
                }
            }
            return new IGPositionsResult(positions, Error: null, HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGPositionsResult(
                Positions: Array.Empty<IGPosition>(),
                Error: ex.Message, HttpStatus: (int)resp.StatusCode);
        }
    }

    // ─── Cash / account ─────────────────────────────────────────

    /// <summary>GET /accounts — returns the configured account's
    /// available balance for sizing decisions.</summary>
    public async Task<IGCashResult> GetCashAsync(CancellationToken ct)
    {
        using var resp = await SendWithAuthAsync(
            HttpMethod.Get, "accounts", null, version: "1", ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            return new IGCashResult(
                Available: null, Balance: null, Currency: null,
                Error: text, HttpStatus: (int)resp.StatusCode);
        }
        try
        {
            using var doc = JsonDocument.Parse(text);
            if (!doc.RootElement.TryGetProperty("accounts", out var accs)
                || accs.ValueKind != JsonValueKind.Array)
            {
                return new IGCashResult(null, null, null,
                    "no accounts array", (int)resp.StatusCode);
            }
            // Pick the configured account or first preferred one.
            JsonElement? target = null;
            foreach (var a in accs.EnumerateArray())
            {
                var id = a.TryGetProperty("accountId", out var ai) ? ai.GetString() : null;
                if (!string.IsNullOrEmpty(_options.AccountId)
                    && string.Equals(id, _options.AccountId, StringComparison.OrdinalIgnoreCase))
                {
                    target = a;
                    break;
                }
                if (a.TryGetProperty("preferred", out var pref)
                    && pref.ValueKind == JsonValueKind.True)
                {
                    target = a;
                }
            }
            target ??= accs[0];
            var bal = target.Value.TryGetProperty("balance", out var b) ? b : default;
            var available = bal.ValueKind == JsonValueKind.Object
                            && bal.TryGetProperty("available", out var av)
                ? av.GetDecimal() : (decimal?)null;
            var balance = bal.ValueKind == JsonValueKind.Object
                          && bal.TryGetProperty("balance", out var bv)
                ? bv.GetDecimal() : (decimal?)null;
            var currency = target.Value.TryGetProperty("currency", out var c) ? c.GetString() : null;
            return new IGCashResult(
                Available: available, Balance: balance,
                Currency: currency, Error: null,
                HttpStatus: (int)resp.StatusCode);
        }
        catch (Exception ex)
        {
            return new IGCashResult(null, null, null, ex.Message, (int)resp.StatusCode);
        }
    }
}

// ─── DTOs ──────────────────────────────────────────────────────

public sealed record IGOrderResult(
    string? DealReference,
    string Status,            // ACCEPTED / REJECTED / UNKNOWN / PARSE_ERROR
    string? StatusReason,
    int HttpStatus);

public sealed record IGPosition(
    string Epic,
    string? InstrumentName,
    string Direction,         // BUY / SELL
    decimal Size,
    decimal EntryLevel,
    string? DealId);

public sealed record IGPositionsResult(
    IReadOnlyList<IGPosition> Positions,
    string? Error,
    int HttpStatus);

public sealed record IGCashResult(
    decimal? Available,
    decimal? Balance,
    string? Currency,
    string? Error,
    int HttpStatus);
