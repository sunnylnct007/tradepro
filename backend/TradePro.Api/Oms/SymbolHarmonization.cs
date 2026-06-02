namespace TradePro.Api.Oms;

/// <summary>
/// Single source of truth for translating an internal ("bare") ticker
/// into the symbol a given broker expects at order placement.
///
/// Why this exists: the strategy path harmonised at enqueue time
/// (TradePlanEndpoints), so strategy orders reached the broker as
/// "AAPL_US_EQ". But MANUAL/external orders enqueued via POST
/// /api/oms/orders carried a bare "AAPL" straight through to placement,
/// and T212 rejected them with HTTP 404 entity-not-found. A silently
/// rejected SELL is a real-money hazard — the operator thinks a position
/// is flat when it is still held. Harmonising at the placement boundary
/// (PostgresOmsService) closes that gap for EVERY order regardless of how
/// it was enqueued, and is idempotent for orders that were already
/// harmonised upstream (the "_" guard below).
/// </summary>
public static class SymbolHarmonization
{
    /// <summary>
    /// Map <paramref name="symbol"/> to the placement symbol for
    /// <paramref name="brokerLabel"/>. Idempotent: an already-harmonised
    /// symbol (e.g. "AAPL_US_EQ") is returned unchanged.
    /// </summary>
    /// <summary>
    /// Strip a symbol back to its natural "source" ticker (the key used in
    /// broker_ticker_map.source_ticker): "AAPL_US_EQ" → "AAPL",
    /// "UA.D.AVGO.CASH.IP" → "AVGO", "AAPL" → "AAPL". Used to look up the
    /// per-broker instrument code in config.
    /// </summary>
    public static string BareSourceTicker(string symbol)
    {
        var s = symbol.Trim().ToUpperInvariant();
        if (s.EndsWith(".IP", StringComparison.Ordinal) && s.Contains(".D."))
        {
            var parts = s.Split('.');
            if (parts.Length >= 4) return parts[2];
        }
        var u = s.IndexOf('_');
        return u > 0 ? s[..u] : s;
    }

    public static string ToBrokerTicker(string symbol, string brokerLabel)
    {
        var s = symbol.Trim().ToUpperInvariant();

        // T212 — expects "AAPL_US_EQ"-style instrument codes for US
        // equities. Already-suffixed symbols pass through unchanged
        // (idempotent for strategy orders harmonised at enqueue time).
        if (brokerLabel.StartsWith("T212", StringComparison.OrdinalIgnoreCase))
        {
            if (s.Contains('_')) return s;
            return s + "_US_EQ";
        }

        // IG — uses EPICs like "US.D.AAPL.CASH.IP". For now the
        // operator-maintained broker_ticker_map handles the lookup; here
        // we strip any T212 suffix back to the bare ticker so the lookup
        // matches. Full broker_ticker_map joins land in the IG iteration.
        if (brokerLabel.StartsWith("IG", StringComparison.OrdinalIgnoreCase))
        {
            var underscore = s.IndexOf('_');
            return underscore > 0 ? s[..underscore] : s;
        }

        return s;
    }
}
