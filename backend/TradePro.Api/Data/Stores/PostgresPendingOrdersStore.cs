using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Postgres-backed pending-orders queue. The state machine
/// (Pending → Placed/Failed/Rejected) is enforced by a CHECK
/// constraint on the column. The MarkPlaced/MarkFailed/MarkRejected
/// methods are intentionally idempotent-safe — running them twice
/// on the same row is harmless.
///
/// Eviction policy: when the table grows past <c>MaxOrders</c>, the
/// oldest non-pending row is deleted. Pending rows are never evicted
/// (they're the actionable queue; losing one means losing user
/// intent). At ~200-row scale this happens via a single SQL DELETE.
/// </summary>
public sealed class PostgresPendingOrdersStore : IPendingOrdersStore
{
    private const int MaxOrders = 200;
    private readonly NpgsqlDataSource _db;

    public PostgresPendingOrdersStore(NpgsqlDataSource db) { _db = db; }

    public PendingOrder Put(JsonElement payload)
    {
        var order = new PendingOrder(
            OrderId: Guid.NewGuid().ToString("N"),
            Broker: JsonbHelpers.ReadString(payload, "broker") ?? "?",
            BrokerMode: JsonbHelpers.ReadString(payload, "broker_mode") ?? "?",
            StrategyId: JsonbHelpers.ReadString(payload, "strategy_id") ?? "?",
            Symbol: JsonbHelpers.ReadString(payload, "symbol") ?? "?",
            T212Ticker: JsonbHelpers.ReadString(payload, "t212_ticker") ?? "",
            Side: JsonbHelpers.ReadString(payload, "side") ?? "BUY",
            Quantity: JsonbHelpers.ReadInt(payload, "quantity"),
            OrderType: JsonbHelpers.ReadString(payload, "order_type") ?? "MARKET",
            Tag: JsonbHelpers.ReadString(payload, "tag"),
            SuggestedAtUtc: JsonbHelpers.ReadString(payload, "suggested_at_utc") ?? DateTime.UtcNow.ToString("o"),
            BarAtEmitClose: JsonbHelpers.ReadDoubleOrNull(payload, "bar_at_emit_close"),
            BarAtEmitTime: JsonbHelpers.ReadString(payload, "bar_at_emit_time"),
            State: PendingOrderState.Pending,
            ReceivedAtUtc: DateTime.UtcNow,
            DecidedAtUtc: null,
            BrokerOrderId: null,
            BrokerStatus: null,
            RejectionReason: null,
            Error: null,
            ResponseBody: null);

        using var conn = _db.OpenConnection();
        using var tx = conn.BeginTransaction();
        conn.Execute(@"
            INSERT INTO pending_orders
                (order_id, broker, broker_mode, strategy_id, symbol, t212_ticker, side, quantity, order_type,
                 tag, suggested_at_utc, bar_at_emit_close, bar_at_emit_time, state, received_at_utc)
            VALUES
                (@OrderId, @Broker, @BrokerMode, @StrategyId, @Symbol, @T212Ticker, @Side, @Quantity, @OrderType,
                 @Tag, @SuggestedAtUtcParsed, @BarAtEmitClose, @BarAtEmitTimeParsed, @StateText, @ReceivedAtUtc);",
            new
            {
                order.OrderId,
                order.Broker,
                order.BrokerMode,
                order.StrategyId,
                order.Symbol,
                order.T212Ticker,
                order.Side,
                order.Quantity,
                order.OrderType,
                order.Tag,
                SuggestedAtUtcParsed = ParseTimestamp(order.SuggestedAtUtc) ?? order.ReceivedAtUtc,
                order.BarAtEmitClose,
                BarAtEmitTimeParsed = ParseTimestamp(order.BarAtEmitTime),
                StateText = order.State.ToString(),
                order.ReceivedAtUtc,
            },
            transaction: tx);
        EvictIfFull(conn, tx);
        tx.Commit();
        return order;
    }

    public PendingOrder? Get(string orderId)
    {
        using var conn = _db.OpenConnection();
        return ReadOne(conn, orderId);
    }

    public PendingOrder? MarkPlaced(string orderId, long? brokerOrderId, string? brokerStatus, string? responseBody)
    {
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            UPDATE pending_orders SET
                state = 'Placed',
                decided_at_utc = NOW(),
                broker_order_id = @brokerOrderId,
                broker_status = @brokerStatus,
                response_body = @responseBody,
                error = NULL
            WHERE order_id = @orderId;",
            new { orderId, brokerOrderId, brokerStatus, responseBody });
        return ReadOne(conn, orderId);
    }

    public PendingOrder? MarkFailed(string orderId, string error, string? responseBody)
    {
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            UPDATE pending_orders SET
                state = 'Failed',
                decided_at_utc = NOW(),
                error = @error,
                response_body = @responseBody
            WHERE order_id = @orderId;",
            new { orderId, error, responseBody });
        return ReadOne(conn, orderId);
    }

    public PendingOrder? MarkRejected(string orderId, string? reason)
    {
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            UPDATE pending_orders SET
                state = 'Rejected',
                decided_at_utc = NOW(),
                rejection_reason = @reason
            WHERE order_id = @orderId;",
            new { orderId, reason });
        return ReadOne(conn, orderId);
    }

    public int RejectAllPending(string? tickerLikePattern, string? reason)
    {
        using var conn = _db.OpenConnection();
        var (where, args) = tickerLikePattern is null
            ? ("WHERE state = 'Pending'",
                (object)new { reason = reason ?? "bulk_reject" })
            : ("WHERE state = 'Pending' AND t212_ticker LIKE @pattern",
                (object)new { reason = reason ?? "bulk_reject", pattern = tickerLikePattern });
        return conn.Execute($@"
            UPDATE pending_orders SET
                state = 'Rejected',
                decided_at_utc = NOW(),
                rejection_reason = @reason
            {where};",
            args);
    }

    public IReadOnlyList<PendingOrder> List(int limit = 200)
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<PendingOrderRow>(@"
            SELECT order_id, broker, broker_mode, strategy_id, symbol, t212_ticker, side, quantity, order_type,
                   tag, suggested_at_utc, bar_at_emit_close, bar_at_emit_time, state, received_at_utc,
                   decided_at_utc, broker_order_id, broker_status, rejection_reason, error, response_body
            FROM pending_orders
            ORDER BY (CASE WHEN state = 'Pending' THEN 0 ELSE 1 END), received_at_utc DESC
            LIMIT @limit;",
            new { limit }).ToList();
        return rows.Select(ToOrder).ToArray();
    }

    private static PendingOrder? ReadOne(NpgsqlConnection conn, string orderId)
    {
        var row = conn.QueryFirstOrDefault<PendingOrderRow>(@"
            SELECT order_id, broker, broker_mode, strategy_id, symbol, t212_ticker, side, quantity, order_type,
                   tag, suggested_at_utc, bar_at_emit_close, bar_at_emit_time, state, received_at_utc,
                   decided_at_utc, broker_order_id, broker_status, rejection_reason, error, response_body
            FROM pending_orders WHERE order_id = @orderId;",
            new { orderId });
        return row is null ? null : ToOrder(row);
    }

    private static void EvictIfFull(NpgsqlConnection conn, Npgsql.NpgsqlTransaction tx)
    {
        // Find one row to evict if we're past the cap. Never evict
        // a pending row — pending = "action required by the user",
        // dropping one means dropping intent.
        conn.Execute(@"
            DELETE FROM pending_orders WHERE order_id IN (
                SELECT order_id FROM pending_orders
                WHERE state <> 'Pending'
                ORDER BY received_at_utc ASC
                OFFSET @keep
            );",
            new { keep = MaxOrders }, transaction: tx);
    }

    private static PendingOrder ToOrder(PendingOrderRow r) => new(
        OrderId: r.order_id,
        Broker: r.broker,
        BrokerMode: r.broker_mode,
        StrategyId: r.strategy_id,
        Symbol: r.symbol,
        T212Ticker: r.t212_ticker,
        Side: r.side,
        Quantity: r.quantity,
        OrderType: r.order_type,
        Tag: r.tag,
        SuggestedAtUtc: r.suggested_at_utc.ToString("o"),
        BarAtEmitClose: r.bar_at_emit_close,
        BarAtEmitTime: r.bar_at_emit_time?.ToString("o"),
        State: Enum.Parse<PendingOrderState>(r.state),
        ReceivedAtUtc: r.received_at_utc,
        DecidedAtUtc: r.decided_at_utc,
        BrokerOrderId: r.broker_order_id,
        BrokerStatus: r.broker_status,
        RejectionReason: r.rejection_reason,
        Error: r.error,
        ResponseBody: r.response_body);

    private static DateTime? ParseTimestamp(string? s)
    {
        if (string.IsNullOrEmpty(s)) return null;
        return DateTime.TryParse(s, null,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal,
            out var dt) ? dt : null;
    }

    // Dapper-projected row shape. snake_case to match the SQL columns;
    // Dapper maps these to the projection record without ceremony.
    private sealed record PendingOrderRow(
        string order_id, string broker, string broker_mode, string strategy_id,
        string symbol, string t212_ticker, string side, int quantity, string order_type,
        string? tag, DateTime suggested_at_utc, double? bar_at_emit_close, DateTime? bar_at_emit_time,
        string state, DateTime received_at_utc, DateTime? decided_at_utc,
        long? broker_order_id, string? broker_status, string? rejection_reason,
        string? error, string? response_body);
}
