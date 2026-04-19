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
}
