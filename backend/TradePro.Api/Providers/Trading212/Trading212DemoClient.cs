using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Demo-only Trading 212 client, dedicated to order placement.
///
/// Why a separate class instead of toggling Trading212Client between
/// modes? Two reasons. (1) The base URL is hard-coded to
/// demo.trading212.com so a config typo cannot route real orders to
/// the live account — separation by type is stronger than separation
/// by string. (2) Reads (portfolio, status, instruments) genuinely
/// belong on live — the user owns positions there, not in demo — so
/// having one client try to serve both intents is a category error.
///
/// Mirrors PlaceMarketOrderAsync on Trading212Client but with demo
/// options binding. Auth scheme detection (key+secret HTTP Basic vs
/// raw key in Authorization header) is the same as live.
/// </summary>
public sealed class Trading212DemoClient
{
    private readonly HttpClient _http;
    private readonly Trading212DemoOptions _options;
    private readonly ILogger<Trading212DemoClient> _log;

    public Trading212DemoClient(
        HttpClient http,
        IOptions<Trading212DemoOptions> options,
        ILogger<Trading212DemoClient> log)
    {
        _http = http;
        _options = options.Value;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.BaseUrl);
            if (!string.IsNullOrWhiteSpace(_options.ApiSecret))
            {
                var token = Convert.ToBase64String(
                    Encoding.UTF8.GetBytes($"{_options.ApiKey}:{_options.ApiSecret}"));
                _http.DefaultRequestHeaders.Authorization =
                    new AuthenticationHeaderValue("Basic", token);
            }
            else
            {
                _http.DefaultRequestHeaders.TryAddWithoutValidation(
                    "Authorization", _options.ApiKey);
            }
            _http.Timeout = TimeSpan.FromSeconds(_options.TimeoutSeconds);
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string Mode => "demo";

    /// <summary>Open positions in the DEMO account. Distinct from the
    /// live client's GetPositionsAsync — needed because demo orders
    /// create demo positions, not live ones, so the UI's "what
    /// happened after I approved that NVDA buy" view has to read from
    /// demo.trading212.com to see the result.</summary>
    public async Task<Trading212PositionsResult> GetPositionsAsync(
        CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212PositionsResult(
                Positions: Array.Empty<Trading212Position>(),
                Error: "demo integration disabled");
        }
        try
        {
            using var resp = await _http.GetAsync("equity/positions", ct);
            if (!resp.IsSuccessStatusCode)
            {
                var body = await SafeReadBodySnippet(resp, ct);
                _log.LogWarning(
                    "T212 demo positions fetch returned HTTP {Status} body={Body}",
                    (int)resp.StatusCode, body);
                return new Trading212PositionsResult(
                    Positions: Array.Empty<Trading212Position>(),
                    Error: $"HTTP {(int)resp.StatusCode} from T212 demo{(string.IsNullOrEmpty(body) ? "" : ": " + body)}",
                    HttpStatus: (int)resp.StatusCode);
            }
            var items = await resp.Content
                .ReadFromJsonAsync<List<Trading212Position>>(cancellationToken: ct);
            return new Trading212PositionsResult(
                Positions: items ?? new List<Trading212Position>(),
                Error: null);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "T212 demo positions fetch failed");
            return new Trading212PositionsResult(
                Positions: Array.Empty<Trading212Position>(),
                Error: ex.Message);
        }
    }

    /// <summary>Place a market order against demo.trading212.com.
    /// Sign convention: positive quantity = BUY, negative = SELL.
    /// Returns a structured result so the approve handler can record
    /// the placement event with the broker-side order id.</summary>
    public async Task<Trading212PlaceResult> PlaceMarketOrderAsync(
        string ticker, decimal signedQuantity, CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212PlaceResult(
                OrderId: null, Status: null, Error: "demo integration disabled",
                HttpStatus: 0, ResponseBody: null);
        }
        var body = new { ticker, quantity = signedQuantity };
        try
        {
            using var resp = await _http.PostAsJsonAsync("equity/orders/market", body, ct);
            // Read the body ONCE — HTTP content is a single-shot stream.
            // Keep the full payload for JSON parsing; truncate only the
            // snippet that goes into logs / DB. The earlier code parsed
            // the truncated snippet and choked on the trailing "…" at
            // byte 203 ("Expected end of string, but instead reached
            // end of data. BytePositionInLine: 203"), so every approve
            // surfaced as a JSON parse error even when T212 had
            // accepted the order cleanly.
            var fullBody = await SafeReadFullBody(resp, ct);
            var snippet = Snip(fullBody);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "T212 demo place-order returned HTTP {Status} ticker={Ticker} qty={Qty} body={Body}",
                    (int)resp.StatusCode, ticker, signedQuantity, snippet);
                return new Trading212PlaceResult(
                    OrderId: null, Status: null,
                    Error: $"HTTP {(int)resp.StatusCode}: {snippet}",
                    HttpStatus: (int)resp.StatusCode, ResponseBody: snippet);
            }
            long? orderId = null;
            string? status = null;
            if (!string.IsNullOrWhiteSpace(fullBody))
            {
                try
                {
                    using var doc = System.Text.Json.JsonDocument.Parse(fullBody);
                    var root = doc.RootElement;
                    if (root.TryGetProperty("id", out var idEl)
                        && idEl.ValueKind == System.Text.Json.JsonValueKind.Number)
                    {
                        orderId = idEl.GetInt64();
                    }
                    if (root.TryGetProperty("status", out var stEl)
                        && stEl.ValueKind == System.Text.Json.JsonValueKind.String)
                    {
                        status = stEl.GetString();
                    }
                }
                catch (System.Text.Json.JsonException ex)
                {
                    // Body shape changed or response was non-JSON. Log
                    // and keep going — the order WAS accepted (we got
                    // 2xx), so don't fail the approve. Leave orderId
                    // null; the operator can reconcile via T212's UI.
                    _log.LogWarning(ex,
                        "T212 demo place-order body wasn't parseable JSON: {Body}",
                        snippet);
                }
            }
            return new Trading212PlaceResult(
                OrderId: orderId, Status: status, Error: null,
                HttpStatus: (int)resp.StatusCode, ResponseBody: snippet);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "T212 demo place-order threw ticker={Ticker} qty={Qty}",
                ticker, signedQuantity);
            return new Trading212PlaceResult(
                OrderId: null, Status: null, Error: ex.Message,
                HttpStatus: 0, ResponseBody: null);
        }
    }

    /// <summary>Cancel a working order via DELETE /equity/orders/{id}.
    /// Used by OmsService.CancelAsync when the operator flips
    /// Auto → Manual and we need to wipe in-flight broker orders.
    /// `brokerOrderId` is the T212-side id (oms_orders.broker_order_id).
    /// Returns Ok=true on 2xx; surfaces the response body on any
    /// failure so the operator sees WHY the cancel didn't land.</summary>
    public async Task<Trading212CancelResult> CancelOrderAsync(
        string brokerOrderId, CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212CancelResult(
                Ok: false, Error: "demo integration disabled",
                HttpStatus: 0, ResponseBody: null);
        }
        try
        {
            using var resp = await _http.DeleteAsync($"equity/orders/{brokerOrderId}", ct);
            var snippet = Snip(await SafeReadFullBody(resp, ct));
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "T212 demo cancel-order returned HTTP {Status} brokerOrderId={Id} body={Body}",
                    (int)resp.StatusCode, brokerOrderId, snippet);
                return new Trading212CancelResult(
                    Ok: false,
                    Error: $"HTTP {(int)resp.StatusCode}: {snippet}",
                    HttpStatus: (int)resp.StatusCode,
                    ResponseBody: snippet);
            }
            return new Trading212CancelResult(
                Ok: true, Error: null,
                HttpStatus: (int)resp.StatusCode,
                ResponseBody: snippet);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "T212 demo cancel-order threw brokerOrderId={Id}", brokerOrderId);
            return new Trading212CancelResult(
                Ok: false, Error: ex.Message,
                HttpStatus: 0, ResponseBody: null);
        }
    }

    /// <summary>Fetch account cash from /equity/account/cash. This is
    /// the T212 INVEST product's cash (stocks/ETFs); T212 CFD (FX +
    /// leveraged) is a separate product with its own /cfd/* endpoints
    /// and is not covered here yet — see follow-up task.</summary>
    public async Task<Trading212CashResult> GetCashAsync(CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212CashResult(
                Free: null, Invested: null, Total: null, Blocked: null,
                Ppl: null, Currency: null,
                Error: "demo integration disabled", HttpStatus: 0);
        }
        try
        {
            using var resp = await _http.GetAsync("equity/account/cash", ct);
            if (!resp.IsSuccessStatusCode)
            {
                var body = Snip(await SafeReadFullBody(resp, ct));
                _log.LogWarning(
                    "T212 demo /account/cash HTTP {Status}: {Body}",
                    (int)resp.StatusCode, body);
                return new Trading212CashResult(
                    Free: null, Invested: null, Total: null, Blocked: null,
                    Ppl: null, Currency: null,
                    Error: $"HTTP {(int)resp.StatusCode}: {body}",
                    HttpStatus: (int)resp.StatusCode);
            }
            var fullBody = await SafeReadFullBody(resp, ct);
            decimal? Pick(System.Text.Json.JsonElement root, string name)
            {
                if (root.TryGetProperty(name, out var el)
                    && el.ValueKind == System.Text.Json.JsonValueKind.Number
                    && el.TryGetDecimal(out var v))
                    return v;
                return null;
            }
            try
            {
                using var doc = System.Text.Json.JsonDocument.Parse(fullBody);
                var r = doc.RootElement;
                return new Trading212CashResult(
                    Free: Pick(r, "free"),
                    Invested: Pick(r, "invested"),
                    Total: Pick(r, "total"),
                    Blocked: Pick(r, "blocked"),
                    Ppl: Pick(r, "ppl"),
                    Currency: r.TryGetProperty("currency", out var cur)
                        ? cur.GetString() : null,
                    Error: null,
                    HttpStatus: (int)resp.StatusCode);
            }
            catch (System.Text.Json.JsonException ex)
            {
                _log.LogWarning(ex,
                    "T212 demo /account/cash body unparseable: {Body}",
                    Snip(fullBody));
                return new Trading212CashResult(
                    Free: null, Invested: null, Total: null, Blocked: null,
                    Ppl: null, Currency: null,
                    Error: ex.Message,
                    HttpStatus: (int)resp.StatusCode);
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "T212 demo /account/cash threw");
            return new Trading212CashResult(
                Free: null, Invested: null, Total: null, Blocked: null,
                Ppl: null, Currency: null,
                Error: ex.Message, HttpStatus: 0);
        }
    }

    /// <summary>Lightweight status probe — does the demo key auth at
    /// the /equity/account/cash endpoint? Used by the status endpoint
    /// to surface demo-side reachability alongside live.</summary>
    public async Task<Trading212Status> GetStatusAsync(CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212Status(
                Configured: false, Mode: "demo", Reachable: false,
                Authenticated: false, Detail: "demo integration disabled",
                RateLimitRemaining: null);
        }
        try
        {
            using var resp = await _http.GetAsync("equity/account/cash", ct);
            var rateLimit = resp.Headers.TryGetValues(
                "X-RateLimit-Remaining", out var vals)
                    ? int.TryParse(vals.FirstOrDefault(), out var n) ? (int?)n : null
                    : null;
            if (resp.IsSuccessStatusCode)
            {
                return new Trading212Status(
                    Configured: true, Mode: "demo", Reachable: true,
                    Authenticated: true, Detail: $"OK (HTTP {(int)resp.StatusCode})",
                    RateLimitRemaining: rateLimit);
            }
            var body = await SafeReadBodySnippet(resp, ct);
            return new Trading212Status(
                Configured: true, Mode: "demo", Reachable: true,
                Authenticated: false,
                Detail: $"HTTP {(int)resp.StatusCode}{(string.IsNullOrEmpty(body) ? "" : " — " + body)}",
                RateLimitRemaining: rateLimit);
        }
        catch (Exception ex)
        {
            return new Trading212Status(
                Configured: true, Mode: "demo", Reachable: false,
                Authenticated: false, Detail: ex.Message,
                RateLimitRemaining: null);
        }
    }

    private static async Task<string> SafeReadBodySnippet(
        HttpResponseMessage resp, CancellationToken ct)
    {
        try
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            return Snip(body);
        }
        catch
        {
            return string.Empty;
        }
    }

    /// Full body read — for paths that need to JSON-parse the response.
    /// Pair with <see cref="Snip"/> on the same string when also
    /// storing / logging, so the persisted snippet stays bounded but
    /// the parser sees valid JSON.
    private static async Task<string> SafeReadFullBody(
        HttpResponseMessage resp, CancellationToken ct)
    {
        try { return await resp.Content.ReadAsStringAsync(ct); }
        catch { return string.Empty; }
    }

    private static string Snip(string body)
        => body.Length > 200 ? body[..200] + "…" : body;
}
