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

    /// <summary>Cheap reachability probe — single GET against the
    /// /quote endpoint (works on any symbol, returns price + change).
    /// Returns HTTP status code; throws nothing. Used by the
    /// integrations-health endpoint to distinguish "Finnhub is happy
    /// but AAPL has no earnings this month" from "Finnhub is 401".</summary>
    public async Task<int?> ProbeAsync(CancellationToken ct)
    {
        if (!_options.IsEnabled) return null;
        var path = $"quote?symbol=AAPL&token={_options.ApiKey}";
        try
        {
            using var resp = await _http.GetAsync(path, ct);
            return (int)resp.StatusCode;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Finnhub probe failed");
            return null;
        }
    }

    /// <summary>
    /// Analyst upgrade / downgrade events for a symbol over a date
    /// window. Returns empty list when Finnhub is disabled OR the
    /// symbol has no events in window; never throws. Free-tier
    /// endpoint, ~60 calls/min limit.
    /// </summary>
    public async Task<IReadOnlyList<FinnhubUpgradeDowngrade>> GetUpgradeDowngradesAsync(
        string symbol,
        DateOnly from,
        DateOnly to,
        CancellationToken ct)
    {
        if (!_options.IsEnabled || string.IsNullOrWhiteSpace(symbol))
        {
            return Array.Empty<FinnhubUpgradeDowngrade>();
        }
        var path =
            $"stock/upgrade-downgrade?symbol={HttpUtility.UrlEncode(symbol.Trim().ToUpperInvariant())}" +
            $"&from={from:yyyy-MM-dd}&to={to:yyyy-MM-dd}" +
            $"&token={_options.ApiKey}";
        try
        {
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning(
                    "Finnhub upgrade-downgrade fetch failed: HTTP {Status} for {Symbol}",
                    (int)resp.StatusCode, symbol);
                return Array.Empty<FinnhubUpgradeDowngrade>();
            }
            var list = await resp.Content.ReadFromJsonAsync<List<FinnhubUpgradeDowngrade>>(
                cancellationToken: ct);
            return (IReadOnlyList<FinnhubUpgradeDowngrade>?)list
                ?? Array.Empty<FinnhubUpgradeDowngrade>();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Finnhub upgrade-downgrade fetch error for {Symbol}", symbol);
            return Array.Empty<FinnhubUpgradeDowngrade>();
        }
    }

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

/// <summary>One row from the Finnhub /calendar/earnings response.
/// Quarter and Year arrive as JSON integers (e.g. "quarter":1, "year":2027) —
/// modelling them as int? avoids the JsonException that swallowed the
/// response into an empty list pre-fix.</summary>
public sealed record FinnhubEarningsEvent(
    string? Symbol,
    string? Date,         // YYYY-MM-DD of the announcement
    decimal? EpsActual,
    decimal? EpsEstimate,
    decimal? RevenueActual,
    decimal? RevenueEstimate,
    string? Hour,         // "bmo" / "amc" / "" — before/after market close
    int? Quarter,
    int? Year);

internal sealed record FinnhubEarningsCalendarResponse(
    [property: System.Text.Json.Serialization.JsonPropertyName("earningsCalendar")]
    List<FinnhubEarningsEvent>? EarningsCalendar);

/// <summary>One row from /stock/upgrade-downgrade — an analyst rating
/// action on a symbol. `Action` is Finnhub's classification:
/// "up" (upgrade), "down" (downgrade), "main" (reiteration),
/// "init" (initiated coverage). `Grade` strings vary by firm
/// ("Buy"/"Hold"/"Outperform"/etc.) — keep raw and let the renderer
/// interpret. <c>SymbolField</c> is the ticker as Finnhub returns it
/// (sometimes empty when querying by symbol).</summary>
public sealed record FinnhubUpgradeDowngrade(
    [property: System.Text.Json.Serialization.JsonPropertyName("symbol")]
    string? SymbolField,
    [property: System.Text.Json.Serialization.JsonPropertyName("gradeTime")]
    long? GradeTime,        // unix epoch seconds — when the rating fired
    [property: System.Text.Json.Serialization.JsonPropertyName("fromGrade")]
    string? FromGrade,
    [property: System.Text.Json.Serialization.JsonPropertyName("toGrade")]
    string? ToGrade,
    [property: System.Text.Json.Serialization.JsonPropertyName("company")]
    string? Company,        // analyst firm name (Goldman Sachs / Citi / etc.)
    [property: System.Text.Json.Serialization.JsonPropertyName("action")]
    string? Action);        // up / down / main / init
