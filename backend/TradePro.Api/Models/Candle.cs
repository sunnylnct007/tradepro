namespace TradePro.Api.Models;

public record Candle(
    DateTime Timestamp,
    decimal Open,
    decimal High,
    decimal Low,
    decimal Close,
    decimal? AdjustedClose,
    long Volume)
{
    /// <summary>
    /// The price every indicator, signal, and backtest should use when
    /// reading a historical series. Prefer AdjustedClose so splits and
    /// cash distributions don't corrupt SMAs, RSI, drawdowns, or equity
    /// curves. Falls back to raw Close for the rare instrument Yahoo
    /// returns without an adjclose stream (e.g. some FX/crypto symbols).
    ///
    /// Use raw <c>Close</c> only when you specifically need the printed
    /// price on that bar (e.g. matching the user's broker statement).
    /// </summary>
    public decimal AdjOrClose => AdjustedClose ?? Close;
}

public record CandleSeries(
    string Symbol,
    string Interval,
    string Provider,
    IReadOnlyList<Candle> Candles,
    // Provenance (bar_cache provider; null from live providers). A grade or a
    // chart is not complete without the newest bar's timestamp beside it.
    IReadOnlyDictionary<string, int>? Sources = null,
    DateTime? NewestBar = null,
    string? Location = null,
    // Why the series is shorter than asked, in words. Set when the store could
    // not be read (denied, unreachable, corrupt) so a screen shows the reason
    // beside an empty chart instead of a 500 or a silent fallback.
    string? Note = null);
