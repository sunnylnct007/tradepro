namespace TradePro.Api.Simulation;

public static class Indicators
{
    public static decimal?[] Sma(IReadOnlyList<decimal> values, int window)
    {
        var result = new decimal?[values.Count];
        if (window <= 0 || values.Count < window) return result;
        decimal sum = 0;
        for (var i = 0; i < values.Count; i++)
        {
            sum += values[i];
            if (i >= window) sum -= values[i - window];
            if (i >= window - 1) result[i] = sum / window;
        }
        return result;
    }

    /// Wilder-smoothed RSI over `period` bars. Index aligned to `values`.
    public static decimal?[] Rsi(IReadOnlyList<decimal> values, int period = 14)
    {
        var result = new decimal?[values.Count];
        if (values.Count <= period) return result;

        decimal gainAvg = 0, lossAvg = 0;
        for (var i = 1; i <= period; i++)
        {
            var d = values[i] - values[i - 1];
            if (d > 0) gainAvg += d; else lossAvg -= d;
        }
        gainAvg /= period;
        lossAvg /= period;
        result[period] = lossAvg == 0m ? 100m : 100m - 100m / (1m + gainAvg / lossAvg);

        for (var i = period + 1; i < values.Count; i++)
        {
            var d = values[i] - values[i - 1];
            var gain = d > 0 ? d : 0m;
            var loss = d < 0 ? -d : 0m;
            gainAvg = (gainAvg * (period - 1) + gain) / period;
            lossAvg = (lossAvg * (period - 1) + loss) / period;
            result[i] = lossAvg == 0m ? 100m : 100m - 100m / (1m + gainAvg / lossAvg);
        }
        return result;
    }

    /// Standard exponential moving average. Seeded with the first value
    /// so the series is defined from index 0 — common convention.
    public static decimal?[] Ema(IReadOnlyList<decimal> values, int span)
    {
        var result = new decimal?[values.Count];
        if (span <= 0 || values.Count == 0) return result;
        var alpha = 2m / (span + 1);
        decimal prev = values[0];
        result[0] = prev;
        for (var i = 1; i < values.Count; i++)
        {
            prev = alpha * values[i] + (1m - alpha) * prev;
            result[i] = prev;
        }
        return result;
    }

    /// MACD line + signal line + histogram. Returns three aligned series.
    public static (decimal?[] macd, decimal?[] signal, decimal?[] hist)
        Macd(IReadOnlyList<decimal> values, int fast = 12, int slow = 26, int signal = 9)
    {
        var fastE = Ema(values, fast);
        var slowE = Ema(values, slow);
        var macd = new decimal?[values.Count];
        for (var i = 0; i < values.Count; i++)
        {
            if (fastE[i] is { } a && slowE[i] is { } b) macd[i] = a - b;
        }
        var macdNonNull = macd.Select(v => v ?? 0m).ToArray();
        var sig = Ema(macdNonNull, signal);
        var hist = new decimal?[values.Count];
        for (var i = 0; i < values.Count; i++)
        {
            if (macd[i] is { } a && sig[i] is { } b) hist[i] = a - b;
        }
        return (macd, sig, hist);
    }

    /// Donchian channel — rolling N-bar high and low (close-based).
    public static (decimal?[] high, decimal?[] low)
        Donchian(IReadOnlyList<decimal> closes, int lookback)
    {
        var hi = new decimal?[closes.Count];
        var lo = new decimal?[closes.Count];
        for (var i = lookback; i < closes.Count; i++)
        {
            decimal h = decimal.MinValue, l = decimal.MaxValue;
            // exclude the current bar — breakout is "today's close > previous N bars' high"
            for (var j = i - lookback; j < i; j++)
            {
                if (closes[j] > h) h = closes[j];
                if (closes[j] < l) l = closes[j];
            }
            hi[i] = h;
            lo[i] = l;
        }
        return (hi, lo);
    }
}
