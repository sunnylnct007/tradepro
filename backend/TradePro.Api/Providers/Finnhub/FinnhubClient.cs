using System.Net.Http.Json;
using System.Web;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.Finnhub;

/// <summary>
/// Typed HttpClient for Finnhub's REST API.
///
/// Currently exposes the earnings-calendar endpoint (forward-looking
/// announcement dates per symbol) — the gap yfinance leaves: yfinance
/// gives reliable HISTORIC earnings via Ticker.earnings_dates but is
/// patchy on confirmed-future-date data. Finnhub fills that.
///
/// Auth: API key passed as a `token` query parameter (not a header)
/// per Finnhub's REST convention. Wrapped in our own request methods
/// so callers don't have to remember.
/// </summary>
public sealed class FinnhubClient
{
    private readonly HttpClient _http;
    private readonly FinnhubOptions _options;
    private readonly ILogger<FinnhubClient> _log;

    public FinnhubClient(
        HttpClient http,
        IOptions<FinnhubOptions> options,
        ILogger<FinnhubClient> log)
    {
        _http = http;
        _options = options.Value;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.BaseUrl);
            _http.Timeout = TimeSpan.FromSeconds(_options.TimeoutSeconds);
        }
    }

    public bool IsEnabled => _options.IsEnabled;

    /// <summary>
    /// Earnings calendar for a single symbol over a date window.
    /// Returns the upcoming announcements (and recent history if
    /// `from` predates today). Empty list when the integration is
    /// disabled OR Finnhub returns no events; never throws.
    /// </summary>
    public async Task<IReadOnlyList<FinnhubEarningsEvent>> GetEarningsCalendarAsync(
        string symbol,
        DateOnly from,
        DateOnly to,
        CancellationToken ct)
    {
        if (!_options.IsEnabled || string.IsNullOrWhiteSpace(symbol))
        {
            return Array.Empty<FinnhubEarningsEvent>();
        }
        var path =
            $"calendar/earnings?from={from:yyyy-MM-dd}&to={to:yyyy-MM-dd}" +
            $"&symbol={HttpUtility.UrlEncode(symbol.Trim().ToUpperInvariant())}" +
            $"&token={_options.ApiKey}";
        try
        {
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "Finnhub earnings calendar fetch failed: HTTP {Status} for {Symbol}",
                    (int)resp.StatusCode, symbol);
                return Array.Empty<FinnhubEarningsEvent>();
            }
            var envelope = await resp.Content.ReadFromJsonAsync<FinnhubEarningsCalendarResponse>(
                cancellationToken: ct);
            return (IReadOnlyList<FinnhubEarningsEvent>?)envelope?.EarningsCalendar
                ?? Array.Empty<FinnhubEarningsEvent>();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Finnhub earnings calendar fetch error for {Symbol}", symbol);
            return Array.Empty<FinnhubEarningsEvent>();
        }
    }
}

/// <summary>One row from the Finnhub /calendar/earnings response.</summary>
public sealed record FinnhubEarningsEvent(
    string? Symbol,
    string? Date,         // YYYY-MM-DD of the announcement
    decimal? EpsActual,
    decimal? EpsEstimate,
    decimal? RevenueActual,
    decimal? RevenueEstimate,
    string? Hour,         // "bmo" / "amc" / "" — before/after market close
    string? Quarter,
    string? Year);

internal sealed record FinnhubEarningsCalendarResponse(
    [property: System.Text.Json.Serialization.JsonPropertyName("earningsCalendar")]
    List<FinnhubEarningsEvent>? EarningsCalendar);
