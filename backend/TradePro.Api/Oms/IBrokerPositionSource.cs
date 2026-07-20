namespace TradePro.Api.Oms;

/// <summary>A read of a broker's golden-source positions.
/// <para/>
/// <see cref="Ok"/> == false means the read FAILED (auth, network, disabled,
/// paused). Callers MUST NOT treat a failed read as "flat" — doing so would
/// let the reconciler falsely settle every open sell. A clean read with an
/// empty <see cref="Positions"/> list is genuinely flat; a failed read is
/// unknown.</summary>
public sealed record BrokerPositionsRead(
    bool Ok,
    string? Error,
    IReadOnlyList<(string Ticker, decimal Qty)> Positions)
{
    public static BrokerPositionsRead Fail(string error) =>
        new(false, error, Array.Empty<(string, decimal)>());

    public static BrokerPositionsRead From(IReadOnlyList<(string, decimal)> positions) =>
        new(true, null, positions);
}

/// <summary>
/// Broker-agnostic golden-source position accessor for the ONE unified
/// reconciler (<see cref="GoldenSourceReconciler"/>). One implementation per
/// broker; the reconciler treats them identically, so adding a broker means
/// adding a class — never touching the reconcile loop. This is the fix for the
/// per-broker-poller whack-a-mole (T212 had a poller, IG had a poller, IBKR had
/// none, and each had its own edge gaps).
/// </summary>
public interface IBrokerPositionSource
{
    /// <summary>OMS broker label this source reconciles (e.g. "T212_DEMO").</summary>
    string BrokerLabel { get; }

    /// <summary>Read current positions from the broker (the golden source).
    /// Must return <see cref="BrokerPositionsRead.Fail"/> on ANY error rather
    /// than an empty success — the distinction is load-bearing.</summary>
    Task<BrokerPositionsRead> ReadPositionsAsync(CancellationToken ct);

    /// <summary>Best-effort REAL execution price for a broker order id, or null
    /// when the broker can't supply it (aged out of history / no lookup path).
    /// The reconciler NEVER fabricates a price — null → the fill is recorded
    /// 0-flagged and a per-broker backfill patches it later.</summary>
    Task<decimal?> TryGetFillPriceAsync(string brokerOrderId, CancellationToken ct);
}
