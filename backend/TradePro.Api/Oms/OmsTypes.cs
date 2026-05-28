namespace TradePro.Api.Oms;

/// <summary>One row of the oms_orders table, immutable from the API
/// boundary. State transitions return a NEW record so callers can't
/// silently mutate the prior snapshot.</summary>
public sealed record OmsOrder(
    Guid Id,
    Guid ClientOrderId,
    string Broker,
    string? BrokerOrderId,
    string? StrategyId,
    string Symbol,
    string Side,
    decimal Qty,
    string OrderType,
    decimal? LimitPrice,
    decimal? StopPrice,
    string TimeInForce,
    string State,
    string PlacedBy,
    decimal FilledQty,
    decimal? AvgFillPrice,
    string? CancelledReason,
    DateTime CreatedAtUtc,
    DateTime LastStateChangeAtUtc
);

/// <summary>One row of the oms_order_events table — the audit trail
/// for any state transition the OMS made on an order.</summary>
public sealed record OmsOrderEvent(
    long Id,
    Guid OrderId,
    string EventType,
    string? PriorState,
    string NewState,
    string Actor,
    string? DetailJson,
    DateTime OccurredAtUtc
);

/// <summary>Caller-supplied intent. ClientOrderId is the idempotency
/// key — if a caller retries with the same id, the OMS returns the
/// existing row instead of duplicating.</summary>
public sealed record OrderIntent(
    Guid ClientOrderId,
    string Broker,           // 'PAPER' | 'T212_DEMO' | 'T212_LIVE' | 'IBKR_PAPER' | 'IBKR_LIVE'
    string Symbol,
    string Side,             // 'BUY' | 'SELL'
    decimal Qty,
    string OrderType,        // 'MKT' | 'LMT' | 'STP' | 'STP_LMT'
    string? StrategyId,
    decimal? LimitPrice = null,
    decimal? StopPrice = null,
    string TimeInForce = "DAY",
    string PlacedBy = "STRATEGY_AUTO"
);

/// <summary>Lifecycle values. Mirror the SQL CHECK constraint;
/// keep these in lock-step with migration 009.</summary>
public static class OmsState
{
    public const string PendingApproval = "PENDING_APPROVAL";
    public const string Submitted = "SUBMITTED";
    public const string Working = "WORKING";
    public const string PartiallyFilled = "PARTIALLY_FILLED";
    public const string Filled = "FILLED";
    public const string Cancelled = "CANCELLED";
    public const string Rejected = "REJECTED";
    public const string Expired = "EXPIRED";

    /// <summary>States where the order is not yet terminal — these are
    /// the rows CancelAllOpen and ListOpen target.</summary>
    public static readonly string[] OpenStates =
        new[] { PendingApproval, Submitted, Working, PartiallyFilled };
}

/// <summary>Mode the OMS is operating in. Switching from Auto to Manual
/// triggers an immediate CancelAllOpen with reason 'MODE_FLIP'.</summary>
public enum OmsMode
{
    Manual,
    Auto,
}
