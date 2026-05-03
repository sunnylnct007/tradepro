using System.Text.Json;
using System.Web;
using TradePro.Api.Models;

namespace TradePro.Api.Providers;

/// <summary>
/// Wraps Yahoo Finance's unauthenticated symbol search. We use this
/// for the autocomplete picker so a user typing 'NV' lands on NVDA
/// (and not on a 500 from the candles endpoint).
///
/// Endpoint: https://query2.finance.yahoo.com/v1/finance/search?q=...
/// Returns a 'quotes' array — symbol, shortname, exchDisp, quoteType,
/// currency. We map only fields we render and skip rows without a
/// usable ticker (occasionally Yahoo returns OPTIONs without symbols).
/// </summary>
public sealed class YahooSearchProvider
{
    private readonly HttpClient _http;
    private readonly ILogger<YahooSearchProvider> _log;

    public YahooSearchProvider(HttpClient http, ILogger<YahooSearchProvider> log)
    {
        _http = http;
        _log = log;
    }

    public async Task<IReadOnlyList<InstrumentMatch>> SearchAsync(
        string query, int limit, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(query)) return Array.Empty<InstrumentMatch>();
        limit = Math.Clamp(limit, 1, 25);

        var url =
            $"https://query2.finance.yahoo.com/v1/finance/search" +
            $"?q={HttpUtility.UrlEncode(query.Trim())}" +
            $"&quotesCount={limit}&newsCount=0&listsCount=0";

        try
        {
            using var resp = await _http.GetAsync(url, ct);
            resp.EnsureSuccessStatusCode();
            await using var stream = await resp.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
            if (!doc.RootElement.TryGetProperty("quotes", out var quotes)
                || quotes.ValueKind != JsonValueKind.Array)
            {
                return Array.Empty<InstrumentMatch>();
            }

            var items = new List<InstrumentMatch>(quotes.GetArrayLength());
            foreach (var q in quotes.EnumerateArray())
            {
                var symbol = TryString(q, "symbol");
                if (string.IsNullOrWhiteSpace(symbol)) continue;
                var name = TryString(q, "shortname")
                    ?? TryString(q, "longname")
                    ?? symbol!;
                items.Add(new InstrumentMatch(
                    Symbol: symbol!,
                    Name: name,
                    Exchange: TryString(q, "exchDisp") ?? TryString(q, "exchange"),
                    Type: TryString(q, "quoteType"),
                    Currency: TryString(q, "currency"),
                    Source: "yahoo"));
            }
            return items;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Yahoo search failed for query '{Q}'", query);
            return Array.Empty<InstrumentMatch>();
        }
    }

    private static string? TryString(JsonElement el, string name) =>
        el.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;
}
