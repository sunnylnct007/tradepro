using System.Text.Json;
using TradePro.Api.Models;
using System.Globalization;

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

    /// <summary>
    /// Fetches historical earnings announcements for <paramref name="symbol"/> via
    /// the Yahoo Finance chart API's <c>events=earnings</c> overlay. Returns a
    /// list of reported earnings only (future / unconfirmed events are skipped).
    /// Sorted oldest-first so the chart can append them in display order.
    ///
    /// Uses the same <see cref="_http"/> HttpClient as <see cref="GetCandlesAsync"/>
    /// so rate-limit headers and user-agent are already set.
    ///
    /// Returns an empty list on any failure — the chart degrades to "no markers"
    /// cleanly instead of erroring out.
    /// </summary>
    public async Task<IReadOnlyList<EarningsMarkerDto>> GetEarningsMarkersAsync(
        string symbol, int lookbackDays, CancellationToken ct)
    {
        try
        {
            var now = DateTimeOffset.UtcNow;
            var period2 = now.ToUnixTimeSeconds();
            var period1 = now.AddDays(-lookbackDays).ToUnixTimeSeconds();

            var url =
                $"https://query1.finance.yahoo.com/v8/finance/chart/{Uri.EscapeDataString(symbol)}" +
                $"?period1={period1}&period2={period2}&interval=1d&events=earnings&includeTimestamps=true";

            using var resp = await _http.GetAsync(url, ct);
            if (!resp.IsSuccessStatusCode)
                return Array.Empty<EarningsMarkerDto>();

            await using var stream = await resp.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

            var chart = doc.RootElement.GetProperty("chart");
            if (!chart.TryGetProperty("result", out var result)
                || result.ValueKind != JsonValueKind.Array
                || result.GetArrayLength() == 0)
                return Array.Empty<EarningsMarkerDto>();

            var first = result[0];
            if (!first.TryGetProperty("events", out var events))
                return Array.Empty<EarningsMarkerDto>();

            if (!events.TryGetProperty("earnings", out var earningsDict)
                || earningsDict.ValueKind != JsonValueKind.Object)
                return Array.Empty<EarningsMarkerDto>();

            var markers = new List<EarningsMarkerDto>();
            foreach (var kv in earningsDict.EnumerateObject())
            {
                var ev = kv.Value;

                // Derive the announcement date. Prefer startdatetime (ISO string)
                // which Yahoo provides per-event; fall back to the unix epoch key.
                string? date = null;
                if (ev.TryGetProperty("startdatetime", out var sdtEl))
                {
                    var sdt = sdtEl.GetString() ?? "";
                    // "2024-10-26T10:30:00-04:00" → take the first 10 chars
                    if (sdt.Length >= 10) date = sdt[..10];
                }
                if (date is null && ev.TryGetProperty("date", out var epochEl))
                {
                    var epoch = epochEl.GetInt64();
                    date = DateTimeOffset.FromUnixTimeSeconds(epoch)
                        .UtcDateTime.Date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
                }
                if (date is null) continue;

                // Only include events with a confirmed epsActual — future
                // earnings have epsActual null and belong to the upcoming-
                // earnings calendar, not the historical chart layer.
                double? epsActual = null;
                if (ev.TryGetProperty("epsActual", out var eaEl) && eaEl.ValueKind != JsonValueKind.Null)
                    epsActual = eaEl.GetDouble();
                if (epsActual is null) continue;

                double? epsEstimate = null;
                if (ev.TryGetProperty("epsEstimate", out var eeEl) && eeEl.ValueKind != JsonValueKind.Null)
                    epsEstimate = eeEl.GetDouble();

                // Yahoo v8 chart returns surprisePercent already as a percentage
                // (e.g. 8.2 means beat by 8.2%). Same unit as the Python layer's
                // Surprise(%) column from yfinance.earnings_dates — no conversion needed.
                double? surprisePct = null;
                if (ev.TryGetProperty("surprisePercent", out var spEl) && spEl.ValueKind != JsonValueKind.Null)
                    surprisePct = spEl.GetDouble();

                markers.Add(new EarningsMarkerDto(date, epsActual, epsEstimate, surprisePct));
            }

            // Sort oldest-first so the chart overlay renders in chronological
            // order and range-based lookups are straightforward.
            return markers
                .OrderBy(m => m.Date, StringComparer.Ordinal)
                .ToList();
        }
        catch
        {
            // Any parse error / network failure → empty list; chart degrades gracefully.
            return Array.Empty<EarningsMarkerDto>();
        }
    }

    /// <summary>
    /// Fetches historical corporate actions (dividends + splits) for
    /// <paramref name="symbol"/> via the Yahoo Finance chart API's
    /// <c>events=div,split</c> overlay. Sorted oldest-first.
    ///
    /// Returns an empty list on any failure — the chart degrades to "no chips"
    /// cleanly instead of erroring out.
    /// </summary>
    public async Task<IReadOnlyList<CorporateActionDto>> GetCorporateActionsAsync(
        string symbol, int lookbackDays, CancellationToken ct)
    {
        try
        {
            var now = DateTimeOffset.UtcNow;
            var period2 = now.ToUnixTimeSeconds();
            var period1 = now.AddDays(-lookbackDays).ToUnixTimeSeconds();

            var url =
                $"https://query1.finance.yahoo.com/v8/finance/chart/{Uri.EscapeDataString(symbol)}" +
                $"?period1={period1}&period2={period2}&interval=1d&events=div%2Csplit";

            using var resp = await _http.GetAsync(url, ct);
            if (!resp.IsSuccessStatusCode)
                return Array.Empty<CorporateActionDto>();

            await using var stream = await resp.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

            var chart = doc.RootElement.GetProperty("chart");
            if (!chart.TryGetProperty("result", out var result)
                || result.ValueKind != JsonValueKind.Array
                || result.GetArrayLength() == 0)
                return Array.Empty<CorporateActionDto>();

            var first = result[0];
            if (!first.TryGetProperty("events", out var events))
                return Array.Empty<CorporateActionDto>();

            var actions = new List<CorporateActionDto>();

            // ── Dividends ──────────────────────────────────────────────────
            if (events.TryGetProperty("dividends", out var divs)
                && divs.ValueKind == JsonValueKind.Object)
            {
                foreach (var kv in divs.EnumerateObject())
                {
                    var ev = kv.Value;
                    var date = EpochToDate(kv.Name);
                    if (date is null) continue;

                    double? amount = null;
                    if (ev.TryGetProperty("amount", out var amtEl) && amtEl.ValueKind != JsonValueKind.Null)
                        amount = amtEl.GetDouble();

                    actions.Add(new CorporateActionDto(date, "dividend", amount, null));
                }
            }

            // ── Splits ─────────────────────────────────────────────────────
            if (events.TryGetProperty("splits", out var splits)
                && splits.ValueKind == JsonValueKind.Object)
            {
                foreach (var kv in splits.EnumerateObject())
                {
                    var ev = kv.Value;
                    var date = EpochToDate(kv.Name);
                    if (date is null) continue;

                    // Yahoo returns numerator/denominator separately and also
                    // a pre-formatted splitRatio string, e.g. "4:1".
                    string? ratio = null;
                    if (ev.TryGetProperty("splitRatio", out var srEl))
                        ratio = srEl.GetString();
                    if (ratio is null)
                    {
                        int? num = ev.TryGetProperty("numerator", out var numEl) ? numEl.GetInt32() : null;
                        int? den = ev.TryGetProperty("denominator", out var denEl) ? denEl.GetInt32() : null;
                        if (num.HasValue && den.HasValue)
                            ratio = $"{num}:{den}";
                    }

                    actions.Add(new CorporateActionDto(date, "split", null, ratio));
                }
            }

            return actions
                .OrderBy(a => a.Date, StringComparer.Ordinal)
                .ToList();
        }
        catch
        {
            return Array.Empty<CorporateActionDto>();
        }
    }

    private static string? EpochToDate(string epochStr)
    {
        if (!long.TryParse(epochStr, out var epoch)) return null;
        return DateTimeOffset.FromUnixTimeSeconds(epoch)
            .UtcDateTime.Date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
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
