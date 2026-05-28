using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Providers;

/// Binance public market-data endpoint for crypto. No API key required for
/// historical klines. Symbols are pairs like BTCUSDT, ETHUSDT.
public sealed class BinanceProvider : IMarketDataProvider
{
    private readonly HttpClient _http;
    public string Name => "binance";

    public BinanceProvider(HttpClient http) => _http = http;

    public async Task<CandleSeries> GetCandlesAsync(
        string symbol, string interval, DateTime from, DateTime to, CancellationToken ct)
    {
        var binanceInterval = MapInterval(interval);
        var startMs = new DateTimeOffset(DateTime.SpecifyKind(from, DateTimeKind.Utc)).ToUnixTimeMilliseconds();
        var endMs = new DateTimeOffset(DateTime.SpecifyKind(to, DateTimeKind.Utc)).ToUnixTimeMilliseconds();

        var candles = new List<Candle>();
        var cursor = startMs;
        while (cursor < endMs)
        {
            var url =
                $"https://api.binance.com/api/v3/klines?symbol={Uri.EscapeDataString(symbol.ToUpperInvariant())}" +
                $"&interval={binanceInterval}&startTime={cursor}&endTime={endMs}&limit=1000";

            using var resp = await _http.GetAsync(url, ct);
            resp.EnsureSuccessStatusCode();
            await using var stream = await resp.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

            if (doc.RootElement.ValueKind != JsonValueKind.Array || doc.RootElement.GetArrayLength() == 0)
                break;

            long lastOpen = cursor;
            foreach (var k in doc.RootElement.EnumerateArray())
            {
                var openMs = k[0].GetInt64();
                var open = decimal.Parse(k[1].GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                var high = decimal.Parse(k[2].GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                var low = decimal.Parse(k[3].GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                var close = decimal.Parse(k[4].GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                var volume = (long)decimal.Parse(k[5].GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                candles.Add(new Candle(
                    DateTimeOffset.FromUnixTimeMilliseconds(openMs).UtcDateTime,
                    open, high, low, close, close, volume));
                lastOpen = openMs;
            }

            if (doc.RootElement.GetArrayLength() < 1000) break;
            cursor = lastOpen + 1;
        }

        return new CandleSeries(symbol, interval, Name, candles);
    }

    private static string MapInterval(string interval) => interval.ToLowerInvariant() switch
    {
        "1m" or "3m" or "5m" or "15m" or "30m" => interval,
        "1h" or "2h" or "4h" or "6h" or "8h" or "12h" => interval,
        "1d" or "3d" or "1w" or "1M" => interval,
        "daily" => "1d",
        "weekly" => "1w",
        "monthly" => "1M",
        _ => "1d"
    };
}
