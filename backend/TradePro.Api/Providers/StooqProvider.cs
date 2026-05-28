using System.Globalization;
using CsvHelper;
using CsvHelper.Configuration;
using TradePro.Api.Models;

namespace TradePro.Api.Providers;

/// Stooq.com CSV endpoint — free, no API key, daily granularity.
/// Equities use the `.us` suffix (e.g. aapl.us); index and FX symbols vary.
public sealed class StooqProvider : IMarketDataProvider
{
    private readonly HttpClient _http;
    public string Name => "stooq";

    public StooqProvider(HttpClient http) => _http = http;

    public async Task<CandleSeries> GetCandlesAsync(
        string symbol, string interval, DateTime from, DateTime to, CancellationToken ct)
    {
        var stooqSymbol = symbol.Contains('.') ? symbol : $"{symbol}.us";
        var stooqInterval = interval.ToLowerInvariant() switch
        {
            "1d" or "daily" => "d",
            "1wk" or "weekly" => "w",
            "1mo" or "monthly" => "m",
            _ => "d"
        };

        var url =
            $"https://stooq.com/q/d/l/?s={Uri.EscapeDataString(stooqSymbol.ToLowerInvariant())}" +
            $"&d1={from:yyyyMMdd}&d2={to:yyyyMMdd}&i={stooqInterval}";

        using var resp = await _http.GetAsync(url, ct);
        resp.EnsureSuccessStatusCode();
        var content = await resp.Content.ReadAsStringAsync(ct);

        // Stooq returns "No data" for unknown symbols with a 200.
        if (string.IsNullOrWhiteSpace(content) || content.StartsWith("No data"))
        {
            return new CandleSeries(symbol, interval, Name, Array.Empty<Candle>());
        }

        using var reader = new StringReader(content);
        var cfg = new CsvConfiguration(CultureInfo.InvariantCulture) { HasHeaderRecord = true };
        using var csv = new CsvReader(reader, cfg);
        await csv.ReadAsync();
        csv.ReadHeader();

        var candles = new List<Candle>();
        while (await csv.ReadAsync())
        {
            if (!DateTime.TryParse(csv.GetField("Date"), CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal, out var date)) continue;
            var open = csv.GetField<decimal>("Open");
            var high = csv.GetField<decimal>("High");
            var low = csv.GetField<decimal>("Low");
            var close = csv.GetField<decimal>("Close");
            var volume = csv.TryGetField<long>("Volume", out var v) ? v : 0L;
            candles.Add(new Candle(date.ToUniversalTime(), open, high, low, close, close, volume));
        }

        return new CandleSeries(symbol, interval, Name, candles);
    }
}
