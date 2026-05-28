namespace TradePro.Api.Oms;

/// <summary>
/// The single placement path every order goes through — manual, paper,
/// algo-driven. Owns the state machine. T212 / IBKR clients plug in as
/// brokers behind ApproveAsync (Phase 2); Phase 1 leaves the broker
/// call as a no-op so the lifecycle + persistence + audit trail can
/// land independently.
///
/// Idempotent on `ClientOrderId` — replaying the same intent returns
/// the prior row. Lets the daemon retry safely on transient failures.
/// </summary>
public interface IOmsService
{
    /// <summary>Insert a new PENDING_APPROVAL row (or return the
    /// existing row when ClientOrderId already exists).</summary>
    Task<OmsOrder> EnqueueAsync(OrderIntent intent, string actor);

    /// <summary>Lookup by internal id. null when missing.</summary>
    Task<OmsOrder?> GetAsync(Guid orderId);

    /// <summary>List orders. `states` filters; null/empty = all states.
    /// Newest first. Caller decides `limit`.</summary>
    Task<IReadOnlyList<OmsOrder>> ListAsync(IReadOnlyCollection<string>? states, int limit);

    /// <summary>PENDING_APPROVAL → SUBMITTED. Phase 1 stub: marks the
    /// row submitted. Phase 2 will actually post to the broker here.</summary>
    Task<OmsOrder> ApproveAsync(Guid orderId, string actor);

    /// <summary>PENDING_APPROVAL → REJECTED.</summary>
    Task<OmsOrder> RejectAsync(Guid orderId, string actor, string reason);

    /// <summary>(WORKING | PARTIALLY_FILLED | SUBMITTED) → CANCELLED.
    /// `reason` lands in cancelled_reason + the event detail.</summary>
    Task<OmsOrder> CancelAsync(Guid orderId, string actor, string reason);

    /// <summary>Cancel every order still in an OpenState. Used by the
    /// auto→manual mode flip to clear any in-flight orders. Returns
    /// the IDs that were cancelled (for the caller's audit log).</summary>
    Task<IReadOnlyList<Guid>> CancelAllOpenAsync(string actor, string reason);

    /// <summary>Record a fill chunk. Updates filled_qty + avg_fill_price
    /// on the parent and transitions to PARTIALLY_FILLED or FILLED.</summary>
    Task<OmsOrder> RecordFillAsync(
        Guid orderId,
        decimal qty,
        decimal price,
        decimal fee,
        string currency,
        string? brokerFillId,
        string actor);

    /// <summary>Event trail for an order, oldest first.</summary>
    Task<IReadOnlyList<OmsOrderEvent>> ListEventsAsync(Guid orderId);

    /// <summary>Derive net position per (strategy_id, symbol) from
    /// FILLED + PARTIALLY_FILLED rows. BUY adds, SELL subtracts.
    /// Strategies seed _fx_positions from this on session_start so
    /// rerunning a strategy doesn't double up on intents
    /// ("continuous optimization" — task #28). `strategyId` filters;
    /// null returns positions across every strategy.</summary>
    Task<IReadOnlyList<OmsPosition>> ListPositionsAsync(string? strategyId);
}

/// <summary>One row of the OMS-derived position view. quantity is
/// signed (BUY positive, SELL negative). avgPrice is the weighted
/// average across the contributing fills, or null when net qty is 0.</summary>
public sealed record OmsPosition(
    string StrategyId,
    string Symbol,
    string Broker,
    decimal Quantity,
    decimal? AvgPrice,
    DateTime LastFillAtUtc
);

/// <summary>Global OMS mode store. Auto = strategy intents auto-approve.
/// Manual = intents sit in PENDING_APPROVAL until a human approves
/// via /api/oms/orders/{id}/approve. Flipping Auto→Manual calls
/// IOmsService.CancelAllOpenAsync internally so the system never holds
/// orphan orders the operator forgot they queued.</summary>
public interface IOmsModeService
{
    OmsMode Current { get; }
    Task<OmsMode> SetAsync(OmsMode target, string actor);
}
