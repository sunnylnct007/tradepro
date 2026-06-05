namespace TradePro.Api.Positions;

/// <summary>
/// Pure, dependency-free P&amp;L attribution helpers for GET
/// /api/pnl/by-strategy — the per-strategy ranking surface. NO DB, NO HTTP,
/// NO clock: callers fetch the broker / OMS numbers and hand them to these
/// functions, exactly like <see cref="PnlReconciliation"/>. That keeps the
/// money math unit-testable in isolation and the endpoint a thin "fetch then
/// hand off" shell.
///
/// The hard piece is REALISED P&amp;L for a T212 strategy: T212 gives us NO
/// realised feed, so the only way to know what an equity sleeve has BANKED is
/// to replay its own fill ledger. <see cref="FifoRealised"/> does that — a
/// long-only FIFO match of SELL fills against the oldest open BUY lots, per
/// (strategy, symbol). It is deliberately conservative: it ONLY realises a
/// sell against a prior buy it can see, never invents a basis, and reports how
/// many sells went unmatched so the caller can flag "ledger incomplete" rather
/// than publish a wrong number.
/// </summary>
public static class PnlByStrategy
{
    /// <summary>One executed fill from the OMS ledger, already ordered by
    /// fill time by the caller. <paramref name="Side"/> is "BUY" / "SELL"
    /// (case-insensitive); <paramref name="Qty"/> is always positive.</summary>
    public readonly record struct Fill(string Side, decimal Qty, decimal Price);

    /// <summary>Outcome of replaying a (strategy, symbol) fill stream.</summary>
    public readonly record struct RealisedResult(
        decimal Realised,         // banked P&L from matched round-trips
        int ClosedTrades,         // count of SELL fills that closed (any) qty
        int WinningTrades,        // of those, how many netted > 0
        decimal UnmatchedSellQty) // SELL qty with no prior BUY lot to match
    {
        /// <summary>Win rate over closed trades, or null when nothing closed
        /// (so the caller shows "n/a", never 0% on an empty set).</summary>
        public double? WinRatePct =>
            ClosedTrades > 0 ? 100.0 * WinningTrades / ClosedTrades : null;
    }

    /// <summary>
    /// Long-only FIFO realised P&amp;L for ONE (strategy, symbol) fill stream.
    /// BUY fills push lots onto a queue; each SELL consumes from the oldest
    /// lots and realises (sellPrice − lotPrice) × matchedQty. A SELL that runs
    /// out of lots (short / data gap) has its remainder counted in
    /// <see cref="RealisedResult.UnmatchedSellQty"/> and does NOT fabricate a
    /// zero-cost basis. Fills MUST already be in chronological order.
    /// <para/>
    /// "Win" is per SELL fill: the realised P&amp;L attributable to that single
    /// sell (Σ over the lots it consumed) being strictly positive.
    /// </summary>
    public static RealisedResult FifoRealised(IEnumerable<Fill> fillsInOrder)
    {
        // Open BUY lots, oldest at the front. Each entry is (remainingQty, price).
        var lots = new LinkedList<(decimal Qty, decimal Price)>();
        decimal realised = 0m;
        decimal unmatchedSell = 0m;
        int closed = 0, won = 0;

        foreach (var f in fillsInOrder)
        {
            if (f.Qty <= 0m) continue;
            var isBuy = string.Equals(f.Side, "BUY", StringComparison.OrdinalIgnoreCase);
            if (isBuy)
            {
                lots.AddLast((f.Qty, f.Price));
                continue;
            }
            // SELL — consume oldest lots first.
            var remaining = f.Qty;
            decimal sellRealised = 0m;
            bool matchedAny = false;
            while (remaining > 0m && lots.First is { } head)
            {
                var (lotQty, lotPrice) = head.Value;
                var take = Math.Min(remaining, lotQty);
                sellRealised += (f.Price - lotPrice) * take;
                remaining -= take;
                matchedAny = true;
                if (take >= lotQty) lots.RemoveFirst();
                else head.Value = (lotQty - take, lotPrice);
            }
            if (remaining > 0m) unmatchedSell += remaining;
            if (matchedAny)
            {
                realised += sellRealised;
                closed++;
                if (sellRealised > 0m) won++;
            }
        }

        return new RealisedResult(realised, closed, won, unmatchedSell);
    }

    /// <summary>A finished per-strategy row, ready to serialise. Every money /
    /// rate field is nullable — null means "genuinely not available", and the
    /// reason lives in <see cref="Notes"/>. NEVER a fabricated number.</summary>
    public sealed record StrategyPnlRow(
        string StrategyId,
        string Broker,
        string Currency,
        decimal? OpenPnl,
        decimal? RealisedPnl,
        decimal? TotalPnl,
        int Trades,
        double? WinRatePct,
        string Notes);

    /// <summary>Total = open + realised, but ONLY when BOTH are present (so we
    /// never imply a total from a half-known book). Either side null → null.
    /// Never blends across currencies — the caller guarantees one currency per
    /// row, this just adds two same-currency numbers.</summary>
    public static decimal? Total(decimal? openPnl, decimal? realisedPnl) =>
        openPnl is decimal o && realisedPnl is decimal r ? o + r : null;

    /// <summary>Join the notes for a row, dropping blanks and de-duping, into a
    /// single "; "-separated string. Keeps the endpoint from emitting "; ; x".
    /// </summary>
    public static string JoinNotes(params string?[] notes) =>
        string.Join("; ", notes
            .Where(n => !string.IsNullOrWhiteSpace(n))
            .Select(n => n!.Trim())
            .Distinct());
}
