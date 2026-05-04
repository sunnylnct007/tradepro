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
            var token = Convert.ToBase64String(
                Encoding.UTF8.GetBytes($"{_options.ApiKey}:{_options.ApiSecret}"));
            _http.DefaultRequestHeaders.Authorization =
                new AuthenticationHeaderValue("Basic", token);
            _http.Timeout = TimeSpan.FromSeconds(_options.TimeoutSeconds);
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string Mode => _options.Mode;

    /// <summary>Open positions for the authenticated account.
    /// Rate limit is 1 req / 1s per T212 docs. Returns an empty list
    /// on transport / auth errors so the caller can render an "off"
    /// state cleanly.</summary>
    public async Task<IReadOnlyList<Trading212Position>> GetPositionsAsync(
        CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return Array.Empty<Trading212Position>();
        }
        try
        {
            using var resp = await _http.GetAsync("equity/positions", ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "Trading212 positions fetch returned HTTP {Status}",
                    (int)resp.StatusCode);
                return Array.Empty<Trading212Position>();
            }
            var items = await resp.Content
                .ReadFromJsonAsync<List<Trading212Position>>(cancellationToken: ct);
            return (IReadOnlyList<Trading212Position>?)items
                ?? Array.Empty<Trading212Position>();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 positions fetch failed");
            return Array.Empty<Trading212Position>();
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
            using var resp = await _http.GetAsync("equity/account/cash", ct);
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
