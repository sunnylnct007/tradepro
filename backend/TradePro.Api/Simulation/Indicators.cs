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
}
