namespace TradePro.Api.Oms;

/// <summary>
/// Golden-source POSITION reconcile — decide when an in-flight SELL has clearly
/// been executed by the broker even though its per-order status poll never
/// resolved (T212's GetOrderStatusAsync can return null on a transient error,
/// so OmsFillPoller steps 1-3 skip it forever, leaving the order stuck
/// SUBMITTED and the OMS "invested" figure inflated — e.g. AMAT/TSLA sold at
/// T212 but never recorded).
///
/// The broker's POSITION is the golden source (project_broker_is_golden_source):
/// a SELL whose symbol is no longer held has filled. The rules here are
/// deliberately conservative so we never fabricate a fill:
///   • SELL only. A stuck BUY that isn't held may simply not have filled —
///     the ABSENCE of a position is not evidence a buy executed.
///   • Only on a CLEAN position read (caller must gate on Error == null). An
///     errored/empty-due-to-error read is NOT "flat"; treating it as flat
///     would falsely settle every open sell.
///   • Only a FULL exit (symbol netted flat). A still-held-but-reduced symbol
///     is ambiguous (could be a different order) → skip.
///   • Corporate-action aware: a broker that still reports a renamed position
///     under its OLD ticker (LB for BBWI) must not make a BBWI sell look
///     "not held". Both sides canonicalise to the current ticker first.
///
/// Price is NOT decided here — the caller fetches the real execution price
/// from broker history and only falls back to a flagged 0 when unavailable.
/// </summary>
public static class PositionReconcile
{
    // Corporate-action ticker renames (OLD → CURRENT), mirrored from the Python
    // strategies/tradepro_strategies/ticker_renames.py. KEEP IN SYNC until a
    // shared config/DB source exists. Small + slow-moving (renames are rare).
    private static readonly IReadOnlyDictionary<string, string> _renames =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["LB"] = "BBWI",   // L Brands → Bath & Body Works (2021)
            ["FB"] = "META",   // Facebook → Meta Platforms (2022)
        };

    /// <summary>Bare, upper-cased, corporate-action-canonical ticker for
    /// held-vs-order comparison. "AMAT_US_EQ" → "AMAT", "LB_US_EQ" → "BBWI".</summary>
    public static string Canonical(string symbol)
    {
        var bare = SymbolHarmonization.BareSourceTicker(symbol ?? "");
        return _renames.TryGetValue(bare, out var cur) ? cur : bare;
    }

    /// <summary>Fold a broker's position rows into net held quantity keyed by
    /// canonical ticker. Caller passes (ticker, quantity) pairs from a CLEAN
    /// read.</summary>
    public static Dictionary<string, decimal> HeldByCanonical(
        IEnumerable<(string Ticker, decimal Quantity)> positions)
    {
        var held = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
        foreach (var (ticker, qty) in positions)
        {
            var canon = Canonical(ticker);
            if (canon.Length == 0) continue;
            held[canon] = held.TryGetValue(canon, out var e) ? e + qty : qty;
        }
        return held;
    }

    /// <summary>True when a SELL for <paramref name="orderSymbol"/> should be
    /// settled as executed given a CLEAN read of held quantities (keyed by
    /// canonical ticker). Executed ⇔ the symbol is no longer held (net ~ 0).</summary>
    public static bool SellExecuted(
        IReadOnlyDictionary<string, decimal> heldByCanonical,
        string orderSymbol)
    {
        var canon = Canonical(orderSymbol);
        if (!heldByCanonical.TryGetValue(canon, out var q)) return true; // not held → gone
        return System.Math.Abs(q) < 1e-9m;                               // netted flat
    }

    /// <summary>What the reconciler should do with ONE standing (filled) OMS
    /// position given the broker's held quantity for that symbol.</summary>
    public enum StandingAction
    {
        InSync,     // OMS matches the broker — nothing to do
        SkipGrace,  // recently filled — don't race broker settlement
        AutoClear,  // broker holds NONE and the source opted into auto-clear → net to 0
        Flag,       // mismatch we must NOT auto-mutate — surface for a human
    }

    /// <summary>
    /// Pure decision for the standing-position reconcile — extracted so the
    /// safety-critical rule (which drift the background loop may auto-mutate) is
    /// unit-tested without a DB or DI. DELIBERATELY conservative
    /// (feedback_no_false_positives):
    ///   • equal qty → InSync (no action).
    ///   • filled within the grace window → SkipGrace (settlement may still be
    ///     landing at the broker; never reverse a fresh fill).
    ///   • AutoClear ONLY when the source opted in AND the broker holds EXACTLY
    ///     zero — a partial/opposite mismatch is ambiguous (whose strategy owns
    ///     the residual, is the read complete) so it is Flagged, never guessed.
    /// </summary>
    public static StandingAction ClassifyStanding(
        decimal omsQty, decimal brokerQty, DateTime lastFillUtc,
        bool canAutoClear, TimeSpan grace, DateTime nowUtc)
    {
        if (omsQty == brokerQty) return StandingAction.InSync;
        if (lastFillUtc > nowUtc - grace) return StandingAction.SkipGrace;
        if (canAutoClear && brokerQty == 0m) return StandingAction.AutoClear;
        return StandingAction.Flag;
    }
}
