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
    IReadOnlyList<Candle> Candles);
