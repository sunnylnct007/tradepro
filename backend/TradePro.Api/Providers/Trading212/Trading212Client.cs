using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Typed HttpClient for the Trading 212 Public API.
///
/// Only the read-only metadata + portfolio surface is wired up so far.
/// Order placement is intentionally off until we have a one-button UI
/// safety story — the API will happily place a real-money trade with
/// the wrong key.
///
/// Auth: T212's spec (strategies/docs/api.json) declares TWO security
/// schemes:
///   • authWithSecretKey — HTTP Basic with API key as username and
///     API secret as password (older account flow that issued a pair)
///   • legacyApiKeyHeader — Authorization header carrying the raw
///     API key with no prefix (newer account flow, issues a single
///     key only)
/// We pick the scheme based on what the operator gave us: if both
/// ApiKey AND ApiSecret are set → Basic; if only ApiKey → raw
/// header. Misconfiguration earlier was sending Basic with a missing
/// secret which T212 silently 401'd — masquerading as "no positions"
/// in the UI.
/// </summary>
public sealed class Trading212Client
{
    private readonly HttpClient _http;
    private readonly Trading212Options _options;
    private readonly ILogger<Trading212Client> _log;

    public Trading212Client(
        HttpClient http,
        IOptions<Trading212Options> options,
        ILogger<Trading212Client> log)
    {
        _http = http;
        _options = options.Value;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.BaseUrl);
            if (!string.IsNullOrWhiteSpace(_options.ApiSecret))
            {
                // Older T212 accounts: API key + secret pair. HTTP Basic.
                var token = Convert.ToBase64String(
                    Encoding.UTF8.GetBytes($"{_options.ApiKey}:{_options.ApiSecret}"));
                _http.DefaultRequestHeaders.Authorization =
                    new AuthenticationHeaderValue("Basic", token);
            }
            else
            {
                // Newer T212 accounts: single key, raw in Authorization
                // header. .NET rejects unprefixed Authorization via the
                // typed setter — TryAddWithoutValidation gets around it.
                _http.DefaultRequestHeaders.TryAddWithoutValidation(
                    "Authorization", _options.ApiKey);
            }
            _http.Timeout = TimeSpan.FromSeconds(_options.TimeoutSeconds);
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string Mode => _options.Mode;

    /// <summary>Open positions for the authenticated account.
    /// Rate limit is 1 req / 1s per T212 docs.
    ///
    /// Returns a result envelope with both the rows AND any error
    /// so callers can distinguish "really 0 positions" from
    /// "auth failed / endpoint 404'd / network blew up". Surfacing
    /// the diagnostic up to the UI prevents the silent-empty bug
    /// where "Basic" auth made T212 401 and the page just said
    /// "no open positions in your demo account".</summary>
    public async Task<Trading212PositionsResult> GetPositionsAsync(
        CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212PositionsResult(
                Positions: Array.Empty<Trading212Position>(),
                Error: "integration disabled");
        }
        try
        {
            using var resp = await _http.GetAsync("equity/positions", ct);
            if (!resp.IsSuccessStatusCode)
            {
                var body = await SafeReadBodySnippet(resp, ct);
                _log.LogWarning(
                    "Trading212 positions fetch returned HTTP {Status} body={Body}",
                    (int)resp.StatusCode, body);
                return new Trading212PositionsResult(
                    Positions: Array.Empty<Trading212Position>(),
                    Error: $"HTTP {(int)resp.StatusCode} from T212{(string.IsNullOrEmpty(body) ? "" : ": " + body)}",
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
            _log.LogWarning(ex, "Trading212 positions fetch failed");
            return new Trading212PositionsResult(
                Positions: Array.Empty<Trading212Position>(),
                Error: ex.Message);
        }
    }

    private static async Task<string> SafeReadBodySnippet(
        HttpResponseMessage resp, CancellationToken ct)
    {
        try
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            return body.Length > 200 ? body[..200] + "…" : body;
        }
        catch
        {
            return string.Empty;
        }
    }

    /// <summary>Pulls the full instruments registry. Rate limit is
    /// 1 req / 50s per T212 docs — callers should cache aggressively.
    /// Returns an empty list (not a throw) on auth or transport errors
    /// so the caller can fall back to a stale cache.</summary>
    public async Task<IReadOnlyList<Trading212Instrument>> GetInstrumentsAsync(
        CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return Array.Empty<Trading212Instrument>();
        }
        try
        {
            using var resp = await _http.GetAsync("equity/metadata/instruments", ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "Trading212 instruments fetch returned HTTP {Status}",
                    (int)resp.StatusCode);
                return Array.Empty<Trading212Instrument>();
            }
            var items = await resp.Content
                .ReadFromJsonAsync<List<Trading212Instrument>>(cancellationToken: ct);
            return (IReadOnlyList<Trading212Instrument>?)items
                ?? Array.Empty<Trading212Instrument>();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 instruments fetch failed");
            return Array.Empty<Trading212Instrument>();
        }
    }

    /// <summary>Hits <c>/equity/account/cash</c> — smallest authenticated
    /// call we can make to prove credentials and connectivity.</summary>
    public async Task<Trading212Status> GetStatusAsync(CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212Status(
                Configured: false,
                Mode: _options.Mode,
                Reachable: false,
                Authenticated: false,
                Detail: "Trading212 integration disabled or missing credentials.");
        }

        try
        {
            // T212 spec lists /equity/account/summary as the canonical
            // metadata probe; older code targeted /account/cash which
            // some accounts now 404 on. Summary always succeeds when
            // auth is valid.
            using var resp = await _http.GetAsync("equity/account/summary", ct);
            var rateLimitRemaining = TryHeaderInt(resp, "x-ratelimit-remaining");
            if (resp.StatusCode == HttpStatusCode.Unauthorized)
            {
                return new Trading212Status(
                    Configured: true,
                    Mode: _options.Mode,
                    Reachable: true,
                    Authenticated: false,
                    Detail: "401 Unauthorized — check API key/secret pair.",
                    RateLimitRemaining: rateLimitRemaining);
            }
            resp.EnsureSuccessStatusCode();
            return new Trading212Status(
                Configured: true,
                Mode: _options.Mode,
                Reachable: true,
                Authenticated: true,
                Detail: $"OK (HTTP {(int)resp.StatusCode})",
                RateLimitRemaining: rateLimitRemaining);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 status probe failed");
            return new Trading212Status(
                Configured: true,
                Mode: _options.Mode,
                Reachable: false,
                Authenticated: false,
                Detail: ex.Message);
        }
    }

    private static int? TryHeaderInt(HttpResponseMessage resp, string name)
    {
        if (resp.Headers.TryGetValues(name, out var vals)
            && int.TryParse(vals.FirstOrDefault(), out var n))
        {
            return n;
        }
        return null;
    }
}

public sealed record Trading212Status(
    bool Configured,
    string Mode,
    bool Reachable,
    bool Authenticated,
    string Detail,
    int? RateLimitRemaining = null);

/// <summary>Envelope for the positions call so the API endpoint can
/// pass the failure reason (auth fail, 404, network) up to the UI
/// instead of swallowing it as an empty list — that silent failure
/// mode was masquerading as "you have 0 positions" and gaslighting
/// users into thinking T212 was working when it wasn't.
///
/// FromCache + AgeSeconds let the endpoint surface "this is the
/// last successful response, T212 is currently rate-limiting us"
/// rather than nuking the dashboard with a 429 banner.</summary>
public sealed record Trading212PositionsResult(
    IReadOnlyList<Trading212Position> Positions,
    string? Error,
    int? HttpStatus = null,
    bool FromCache = false,
    double? AgeSeconds = null,
    DateTime? FetchedAtUtc = null);

/// <summary>One row from /equity/metadata/instruments. T212 ticker
/// format is &lt;ROOT&gt;_&lt;EXCHANGE&gt;_&lt;TYPE&gt; (e.g. "AAPL_US_EQ"); we keep
/// it as-is and rely on shortName + currencyCode for the user-facing
/// label. Yahoo-side mapping (T212 "AAPL_US_EQ" → Yahoo "AAPL") is
/// non-trivial for non-US venues — we don't do it here.</summary>
public sealed record Trading212Instrument(
    string Ticker,
    string? ShortName,
    string? Name,
    string? CurrencyCode,
    string? Type,
    string? Isin,
    DateTime? AddedOn);

/// <summary>Embedded instrument reference inside a Position. Smaller
/// than Trading212Instrument — T212 strips it down on the positions
/// payload. Just enough to label the row.</summary>
public sealed record Trading212InstrumentRef(
    string? Ticker,
    string? Name,
    string? Currency,
    string? Isin);

/// <summary>One open position from /equity/positions. Notable: T212
/// returns its OWN currentPrice here (different from Yahoo) — useful
/// for reconciling the price the user sees in the broker app vs the
/// Yahoo close that drives our indicators.</summary>
public sealed record Trading212Position(
    string Ticker,
    decimal Quantity,
    decimal? QuantityAvailableForTrading,
    decimal? QuantityInPies,
    decimal? AveragePricePaid,
    decimal? CurrentPrice,
    DateTime? CreatedAt,
    Trading212InstrumentRef? Instrument);
