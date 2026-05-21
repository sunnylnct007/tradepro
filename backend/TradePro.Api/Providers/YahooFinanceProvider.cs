using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Providers;

/// Free, no-key Yahoo Finance chart endpoint. Intended for personal research
/// use — rate limits and ToS apply; for production volume switch to a paid feed.
public sealed class YahooFinanceProvider : IMarketDataProvider
{
    private readonly HttpClient _http;
    public string Name => "yahoo";

    public YahooFinanceProvider(HttpClient http) => _http = http;

    public async Task<CandleSeries> GetCandlesAsync(
        string symbol, string interval, DateTime from, DateTime to, CancellationToken ct)
    {
        var period1 = new DateTimeOffset(DateTime.SpecifyKind(from, DateTimeKind.Utc)).ToUnixTimeSeconds();
        var period2 = new DateTimeOffset(DateTime.SpecifyKind(to, DateTimeKind.Utc)).ToUnixTimeSeconds();
        var yahooInterval = MapInterval(interval);

        var url =
            $"https://query1.finance.yahoo.com/v8/finance/chart/{Uri.EscapeDataString(symbol)}" +
            $"?period1={period1}&period2={period2}&interval={yahooInterval}&events=div%2Csplit";

        using var resp = await _http.GetAsync(url, ct);
        // Yahoo returns 404 for unknown / delisted tickers (e.g. user
        // typed "VGGS" instead of "VGGS.L"). Surface that as an empty
        // CandleSeries so the caller can return a graceful "no data"
        // decision — same path as the empty `result` array below —
        // rather than 500-ing every strategy in a scan because of one
        // typo. Other HTTP errors (rate limit 429, 5xx) still throw
        // so the operator notices a real infra problem.
        if (resp.StatusCode == System.Net.HttpStatusCode.NotFound)
        {
            return new CandleSeries(symbol, interval, Name, Array.Empty<Candle>());
        }
        resp.EnsureSuccessStatusCode();
        await using var stream = await resp.Content.ReadAsStreamAsync(ct);

        using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
        var result = doc.RootElement.GetProperty("chart").GetProperty("result");
        if (result.ValueKind != JsonValueKind.Array || result.GetArrayLength() == 0)
        {
            return new CandleSeries(symbol, interval, Name, Array.Empty<Candle>());
        }

        var first = result[0];
        // Yahoo returns the result entry without `timestamp` when the
        // symbol is invalid / has no bars — surface that as an empty
        // CandleSeries so the caller (SignalEngine, etc.) can return a
        // graceful 'no data' decision instead of 500-ing the user.
        if (!first.TryGetProperty("timestamp", out var tsRaw)
            || tsRaw.ValueKind != JsonValueKind.Array)
        {
            return new CandleSeries(symbol, interval, Name, Array.Empty<Candle>());
        }
        var timestamps = tsRaw.EnumerateArray().Select(e => e.GetInt64()).ToArray();
        if (!first.TryGetProperty("indicators", out var indicators))
        {
            return new CandleSeries(symbol, interval, Name, Array.Empty<Candle>());
        }
        if (!indicators.TryGetProperty("quote", out var quoteArr)
            || quoteArr.ValueKind != JsonValueKind.Array
            || quoteArr.GetArrayLength() == 0)
        {
            return new CandleSeries(symbol, interval, Name, Array.Empty<Candle>());
        }
        var quote = quoteArr[0];

        var opens = ReadNullableDecimals(quote, "open");
        var highs = ReadNullableDecimals(quote, "high");
        var lows = ReadNullableDecimals(quote, "low");
        var closes = ReadNullableDecimals(quote, "close");
        var volumes = ReadNullableLongs(quote, "volume");

        decimal?[]? adj = null;
        if (indicators.TryGetProperty("adjclose", out var adjArr) &&
            adjArr.ValueKind == JsonValueKind.Array && adjArr.GetArrayLength() > 0)
        {
            adj = ReadNullableDecimals(adjArr[0], "adjclose");
        }

        var candles = new List<Candle>(timestamps.Length);
        for (var i = 0; i < timestamps.Length; i++)
        {
            if (opens[i] is null || highs[i] is null || lows[i] is null || closes[i] is null) continue;
            candles.Add(new Candle(
                DateTimeOffset.FromUnixTimeSeconds(timestamps[i]).UtcDateTime,
                opens[i]!.Value, highs[i]!.Value, lows[i]!.Value, closes[i]!.Value,
                adj?[i], volumes[i] ?? 0));
        }

        return new CandleSeries(symbol, interval, Name, candles);
    }

    private static string MapInterval(string interval) => interval.ToLowerInvariant() switch
    {
        "1m" or "2m" or "5m" or "15m" or "30m" or "60m" or "90m" or "1h" => interval,
        "1d" or "5d" or "1wk" or "1mo" or "3mo" => interval,
        "daily" => "1d",
        "weekly" => "1wk",
        "monthly" => "1mo",
        _ => "1d"
    };

    private static decimal?[] ReadNullableDecimals(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return Array.Empty<decimal?>();
        return arr.EnumerateArray()
            .Select(e => e.ValueKind == JsonValueKind.Null ? (decimal?)null : e.GetDecimal())
            .ToArray();
    }

    private static long?[] ReadNullableLongs(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return Array.Empty<long?>();
        return arr.EnumerateArray()
            .Select(e => e.ValueKind == JsonValueKind.Null ? (long?)null : e.GetInt64())
            .ToArray();
    }
}
