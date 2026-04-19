namespace TradePro.Api.Models;

public record Candle(
    DateTime Timestamp,
    decimal Open,
    decimal High,
    decimal Low,
    decimal Close,
    decimal? AdjustedClose,
    long Volume);

public record CandleSeries(
    string Symbol,
    string Interval,
    string Provider,
    IReadOnlyList<Candle> Candles);
