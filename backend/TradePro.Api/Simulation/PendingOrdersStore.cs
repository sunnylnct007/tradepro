using System.Collections.Concurrent;
using System.Text.Json;

namespace TradePro.Api.Simulation;

/// <summary>
/// Pending order queue for the T212 manual-placement flow.
///
/// Lifecycle:
///   1. Mac strategy emits an order in --placement-mode manual
///   2. T212OrderRouter (Mac) POSTs the intent to /api/ingest/paper-pending-order
///   3. Store creates a PENDING entry, returns the order_id
///   4. UI shows the order with Approve / Reject buttons
///   5. Approve → backend places via Trading212Client → state becomes
///      PLACED (with T212 order id + status) OR FAILED (with error)
///   6. Reject → state becomes REJECTED with operator-provided reason
///
/// Terminal states (PLACED / FAILED / REJECTED) stay in the list for
/// audit but the UI filters them under a separate "history" toggle.
/// In-memory + capped at 200 to prevent unbounded growth.
/// </summary>
public interface IPendingOrdersStore
{
    PendingOrder Put(JsonElement payload);
    PendingOrder? Get(string orderId);
    PendingOrder? MarkPlaced(string orderId, long? brokerOrderId, string? brokerStatus, string? responseBody);
    PendingOrder? MarkFailed(string orderId, string error, string? responseBody);
    PendingOrder? MarkRejected(string orderId, string? reason);
    IReadOnlyList<PendingOrder> List(int limit = 200);

    /// <summary>Bulk-reject every Pending row matching the optional
    /// ticker LIKE pattern (SQL LIKE syntax, e.g. "%_US_EQ"). Pass
    /// null pattern to reject ALL Pending rows. Returns the count
    /// rejected. Used to clear stale legacy rows the operator can't
    /// approve (broken ticker mappings, schema changes, etc).</summary>
    int RejectAllPending(string? tickerLikePattern, string? reason);
}

public enum PendingOrderState
{
    Pending,
    Placed,
    Failed,
    Rejected,
}

public sealed record PendingOrder(
    string OrderId,
    string Broker,
    string BrokerMode,
    string StrategyId,
    string Symbol,
    string T212Ticker,
    string Side,
    int Quantity,
    string OrderType,
    string? Tag,
    string SuggestedAtUtc,
    double? BarAtEmitClose,
    string? BarAtEmitTime,
    PendingOrderState State,
    DateTime ReceivedAtUtc,
    DateTime? DecidedAtUtc,
    long? BrokerOrderId,
    string? BrokerStatus,
    string? RejectionReason,
    string? Error,
    string? ResponseBody);

public sealed class InMemoryPendingOrdersStore : IPendingOrdersStore
{
    private const int MaxOrders = 200;
    private readonly ConcurrentDictionary<string, PendingOrder> _byId = new();

    public PendingOrder Put(JsonElement payload)
    {
        var orderId = Guid.NewGuid().ToString("N");
        var order = new PendingOrder(
            OrderId: orderId,
            Broker: ReadString(payload, "broker") ?? "?",
            BrokerMode: ReadString(payload, "broker_mode") ?? "?",
            StrategyId: ReadString(payload, "strategy_id") ?? "?",
            Symbol: ReadString(payload, "symbol") ?? "?",
            T212Ticker: ReadString(payload, "t212_ticker") ?? "",
            Side: ReadString(payload, "side") ?? "?",
            Quantity: ReadInt(payload, "quantity"),
            OrderType: ReadString(payload, "order_type") ?? "MARKET",
            Tag: ReadString(payload, "tag"),
            SuggestedAtUtc: ReadString(payload, "suggested_at_utc") ?? DateTime.UtcNow.ToString("o"),
            BarAtEmitClose: ReadDouble(payload, "bar_at_emit_close"),
            BarAtEmitTime: ReadString(payload, "bar_at_emit_time"),
            State: PendingOrderState.Pending,
            ReceivedAtUtc: DateTime.UtcNow,
            DecidedAtUtc: null,
            BrokerOrderId: null,
            BrokerStatus: null,
            RejectionReason: null,
            Error: null,
            ResponseBody: null);
        _byId[orderId] = order;
        EvictIfFull();
        return order;
    }

    public PendingOrder? Get(string orderId)
        => _byId.TryGetValue(orderId, out var o) ? o : null;

    public PendingOrder? MarkPlaced(string orderId, long? brokerOrderId, string? brokerStatus, string? responseBody)
        => Mutate(orderId, o => o with
        {
            State = PendingOrderState.Placed,
            DecidedAtUtc = DateTime.UtcNow,
            BrokerOrderId = brokerOrderId,
            BrokerStatus = brokerStatus,
            ResponseBody = responseBody,
            Error = null,
        });

    public PendingOrder? MarkFailed(string orderId, string error, string? responseBody)
        => Mutate(orderId, o => o with
        {
            State = PendingOrderState.Failed,
            DecidedAtUtc = DateTime.UtcNow,
            Error = error,
            ResponseBody = responseBody,
        });

    public PendingOrder? MarkRejected(string orderId, string? reason)
        => Mutate(orderId, o => o with
        {
            State = PendingOrderState.Rejected,
            DecidedAtUtc = DateTime.UtcNow,
            RejectionReason = reason,
        });

    public IReadOnlyList<PendingOrder> List(int limit = 200)
        => _byId.Values
            // Pending first (action required), then most-recent of
            // terminal states for audit. State-priority sort first
            // so the UI's default view has the actionable items up top.
            .OrderBy(o => o.State == PendingOrderState.Pending ? 0 : 1)
            .ThenByDescending(o => o.ReceivedAtUtc)
            .Take(limit)
            .ToArray();

    public int RejectAllPending(string? tickerLikePattern, string? reason)
    {
        var count = 0;
        var pattern = tickerLikePattern;
        var reasonText = reason ?? "bulk_reject";
        foreach (var key in _byId.Keys.ToList())
        {
            if (!_byId.TryGetValue(key, out var o)) continue;
            if (o.State != PendingOrderState.Pending) continue;
            // SQL LIKE → simple glob match (% wildcard) for the in-memory
            // path. Used in unit tests; prod hits the Postgres impl.
            if (pattern is not null && !LikeMatch(o.T212Ticker, pattern)) continue;
            _byId[key] = o with
            {
                State = PendingOrderState.Rejected,
                DecidedAtUtc = DateTime.UtcNow,
                RejectionReason = reasonText,
            };
            count++;
        }
        return count;
    }

    private static bool LikeMatch(string value, string pattern)
    {
        // SQL LIKE %x → glob *x. Just convert and use a Regex.
        var rx = "^" + System.Text.RegularExpressions.Regex.Escape(pattern).Replace("%", ".*") + "$";
        return System.Text.RegularExpressions.Regex.IsMatch(value ?? "", rx);
    }

    private PendingOrder? Mutate(string orderId, Func<PendingOrder, PendingOrder> f)
    {
        if (!_byId.TryGetValue(orderId, out var existing)) return null;
        var updated = f(existing);
        _byId[orderId] = updated;
        return updated;
    }

    private void EvictIfFull()
    {
        if (_byId.Count <= MaxOrders) return;
        var oldest = _byId.Values
            .Where(o => o.State != PendingOrderState.Pending) // never evict a pending one
            .OrderBy(o => o.ReceivedAtUtc)
            .FirstOrDefault();
        if (oldest is not null)
        {
            _byId.TryRemove(oldest.OrderId, out _);
        }
    }

    private static string? ReadString(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static int ReadInt(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetInt32() : 0;

    private static double? ReadDouble(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetDouble() : (double?)null;
}
