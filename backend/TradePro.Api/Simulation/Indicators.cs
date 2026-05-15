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

    /// Wilder's Average True Range. Same math as the Python side
    /// (tradepro_strategies.indicators.atr) so a backtest with
    /// ATR-multiplier stops produces the same exit levels as the
    /// comparator's market_state.atr_14 reading on the same data.
    /// First `period` bars are null because there's no prior close
    /// to gap-test against.
    public static decimal?[] Atr(
        IReadOnlyList<decimal> high,
        IReadOnlyList<decimal> low,
        IReadOnlyList<decimal> close,
        int period = 14)
    {
        var n = close.Count;
        var result = new decimal?[n];
        if (n <= period) return result;
        // True Range series (skipping bar 0, which has no prior close).
        var tr = new decimal[n];
        for (var i = 1; i < n; i++)
        {
            var prev = close[i - 1];
            var r1 = high[i] - low[i];
            var r2 = Math.Abs(high[i] - prev);
            var r3 = Math.Abs(low[i] - prev);
            tr[i] = Math.Max(r1, Math.Max(r2, r3));
        }
        // Wilder smoothing: seed with simple average of first `period` TRs,
        // then EMA-style update with alpha = 1/period.
        decimal seed = 0m;
        for (var i = 1; i <= period; i++) seed += tr[i];
        seed /= period;
        result[period] = seed;
        decimal prevAtr = seed;
        for (var i = period + 1; i < n; i++)
        {
            prevAtr = (prevAtr * (period - 1) + tr[i]) / period;
            result[i] = prevAtr;
        }
        return result;
    }

    /// Bollinger Bands — middle = SMA(window), upper/lower = middle
    /// ± num_std * stddev(window). Mirrors tradepro_strategies.indicators.bollinger
    /// so the backtest engine reads the same levels as market_state.
    public static (decimal?[] middle, decimal?[] upper, decimal?[] lower)
        Bollinger(IReadOnlyList<decimal> values, int window = 20, double numStd = 2.0)
    {
        var mid = Sma(values, window);
        var upper = new decimal?[values.Count];
        var lower = new decimal?[values.Count];
        if (window <= 0 || values.Count < window) return (mid, upper, lower);

        var std = (decimal)numStd;
        for (var i = window - 1; i < values.Count; i++)
        {
            if (mid[i] is not { } m) continue;
            decimal sumSq = 0m;
            for (var j = i - window + 1; j <= i; j++)
            {
                var diff = values[j] - m;
                sumSq += diff * diff;
            }
            // Population stddev — matches pandas default ddof=0 used in
            // the Python bollinger() helper. Slight underestimate vs.
            // sample stddev but the comparator and backtester must agree.
            var sd = (decimal)Math.Sqrt((double)(sumSq / window));
            upper[i] = m + std * sd;
            lower[i] = m - std * sd;
        }
        return (mid, upper, lower);
    }

    /// Ichimoku Cloud components. Returns the tenkan (conversion),
    /// kijun (base), and the cloud (max/min of senkou A & B) ALREADY
    /// shifted forward `displacement` bars — index aligned so the
    /// caller can compare `close[i] > cloud_high[i]` directly without
    /// re-shifting. NaN where the lookback isn't long enough yet.
    public static (decimal?[] tenkan, decimal?[] kijun, decimal?[] cloudHigh, decimal?[] cloudLow, decimal?[] senkouB)
        Ichimoku(
            IReadOnlyList<decimal> high,
            IReadOnlyList<decimal> low,
            IReadOnlyList<decimal> close,
            int tenkanP = 9,
            int kijunP = 26,
            int senkouBP = 52,
            int displacement = 26)
    {
        var n = close.Count;
        var tenkan = new decimal?[n];
        var kijun = new decimal?[n];
        var senkouAraw = new decimal?[n];
        var senkouBraw = new decimal?[n];
        var cloudHi = new decimal?[n];
        var cloudLo = new decimal?[n];
        var senkouBshift = new decimal?[n];

        decimal? RollingMidpoint(int idx, int window)
        {
            if (idx < window - 1) return null;
            decimal h = decimal.MinValue, l = decimal.MaxValue;
            for (var j = idx - window + 1; j <= idx; j++)
            {
                if (high[j] > h) h = high[j];
                if (low[j] < l) l = low[j];
            }
            return (h + l) / 2m;
        }

        for (var i = 0; i < n; i++)
        {
            tenkan[i] = RollingMidpoint(i, tenkanP);
            kijun[i] = RollingMidpoint(i, kijunP);
            if (tenkan[i] is { } t && kijun[i] is { } k) senkouAraw[i] = (t + k) / 2m;
            senkouBraw[i] = RollingMidpoint(i, senkouBP);
        }

        // Shift senkou A and B forward by `displacement` bars so the
        // cloud at index i represents the forward projection from
        // (i - displacement). Bars with insufficient history stay null.
        for (var i = 0; i < n; i++)
        {
            var src = i - displacement;
            if (src < 0) continue;
            var a = senkouAraw[src];
            var b = senkouBraw[src];
            if (a is null || b is null) continue;
            cloudHi[i] = Math.Max(a.Value, b.Value);
            cloudLo[i] = Math.Min(a.Value, b.Value);
            senkouBshift[i] = b;
        }
        return (tenkan, kijun, cloudHi, cloudLo, senkouBshift);
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
