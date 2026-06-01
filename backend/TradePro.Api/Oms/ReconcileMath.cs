namespace TradePro.Api.Oms;

/// <summary>
/// Pure reconcile-netting math for /oms/positions/sync-from-broker —
/// extracted from the endpoint so it is unit-testable (the bug it guards
/// against lived in an untested endpoint lambda).
///
/// THE BUG: OMS reports positions per (symbol, strategy), so one symbol can
/// have a strategy bucket (e.g. ichimoku_equity +1.31) AND a null reconcile
/// bucket at the same time. The original code took GroupBy(symbol).First(),
/// seeing only one bucket — so the offsetting fill never cancelled the true
/// net and repeated syncs DIVERGED (the null bucket accumulated junk).
/// Summing across buckets makes the delta the real net, so the sync is
/// idempotent: it converges to the broker's number and then does nothing.
/// (Demo reset → flatten is a FREQUENT operation, so it must be repeatable.)
/// </summary>
public static class ReconcileMath
{
    /// <summary>Reduce a broker symbol/epic to its bare reconciliation key.</summary>
    public static string Bare(string? s)
    {
        var u = (s ?? "").ToUpperInvariant();
        if (u.StartsWith("CS.D.") || u.StartsWith("IX.D."))
        {
            var parts = u.Split('.');
            if (parts.Length >= 4) return parts[2];
        }
        return u.Contains('_') ? u.Split('_')[0] : u;
    }

    public readonly record struct Pos(string Symbol, decimal Qty, decimal? AvgPrice);
    public readonly record struct Adjustment(
        string Symbol, string Side, decimal Qty, decimal Price,
        decimal TargetQty, decimal FromOmsQty);

    /// <summary>
    /// Compute the order adjustments that bring OMS to the broker's per-symbol
    /// net. <paramref name="omsPositions"/> may contain several rows per symbol
    /// (strategy buckets) — they are summed. <paramref name="brokerActuals"/>
    /// is the broker's truth; an empty set means a flat account (close all).
    /// </summary>
    public static List<Adjustment> ComputeAdjustments(
        IEnumerable<Pos> omsPositions, IEnumerable<Pos> brokerActuals)
    {
        var omsByBare = omsPositions
            .GroupBy(p => Bare(p.Symbol))
            .ToDictionary(g => g.Key, g => (
                Qty: g.Sum(p => p.Qty),
                Symbol: g.First().Symbol,
                AvgPrice: g.First().AvgPrice));
        var actualByBare = brokerActuals
            .GroupBy(a => Bare(a.Symbol))
            .ToDictionary(g => g.Key, g => g.First());

        var adjustments = new List<Adjustment>();
        foreach (var bare in omsByBare.Keys.Union(actualByBare.Keys))
        {
            var hasActual = actualByBare.TryGetValue(bare, out var act);
            var hasOms = omsByBare.TryGetValue(bare, out var omsP);
            var omsQty = hasOms ? omsP.Qty : 0m;
            var targetQty = hasActual ? act.Qty : 0m;
            var delta = targetQty - omsQty;
            if (Math.Abs(delta) < 0.0001m) continue;
            var symbol = hasActual ? act.Symbol : omsP.Symbol;
            // Skip rows with no usable symbol (e.g. a T212 cash/pie entry with
            // a null ticker) — can't be a real position + violates NOT NULL.
            if (string.IsNullOrWhiteSpace(symbol)) continue;
            var price = (hasActual ? act.AvgPrice : (hasOms ? omsP.AvgPrice : null)) ?? 0m;
            adjustments.Add(new Adjustment(
                symbol, delta > 0 ? "BUY" : "SELL", Math.Abs(delta), price, targetQty, omsQty));
        }
        return adjustments;
    }
}
