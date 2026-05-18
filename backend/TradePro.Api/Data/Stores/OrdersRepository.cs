using System.Text.Json;
using Dapper;
using Npgsql;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Append-only writer for the orders + fills + events tables. The
/// existing pending_orders queue + paper snapshots are
/// <em>projections</em> of this event log — they get updated as
/// orders flow through, but they're not the source of truth.
///
/// Why a single repository rather than three: orders/fills/events
/// almost always get written in the same transaction (placing an
/// order is "INSERT orders + INSERT events", filling it is
/// "INSERT fills + INSERT events"). Keeping them in one class makes
/// the transactional boundary obvious.
/// </summary>
public sealed class OrdersRepository
{
    private readonly NpgsqlDataSource _db;

    public OrdersRepository(NpgsqlDataSource db) { _db = db; }

    /// <summary>Insert a new row into <c>orders</c> and emit an
    /// <c>order_emitted</c> domain event. Same transaction so an
    /// observer of <c>events</c> can never see an event for an
    /// order that doesn't exist.</summary>
    public OrderRecord Insert(NewOrder o)
    {
        using var conn = _db.OpenConnection();
        using var tx = conn.BeginTransaction();

        var orderId = o.OrderId ?? Guid.NewGuid().ToString("N");
        var emittedAt = o.EmittedAtUtc ?? DateTime.UtcNow;
        var decisionTraceJson = o.DecisionTrace is null
            ? "[]"
            : JsonbHelpers.ToJsonb(o.DecisionTrace.Value);

        conn.Execute(@"
            INSERT INTO orders
                (order_id, correlation_id, strategy_name, strategy_version, params_hash,
                 mode, broker, symbol, side, quantity, order_type, limit_price, stop_price,
                 bar_at_emit_close, bar_at_emit_time, decision_trace, tag, emitted_at_utc)
            VALUES
                (@orderId, @correlationId, @strategyName, @strategyVersion, @paramsHash,
                 @mode, @broker, @symbol, @side, @quantity, @orderType, @limitPrice, @stopPrice,
                 @barAtEmitClose, @barAtEmitTime, @decisionTrace::jsonb, @tag, @emittedAt);",
            new
            {
                orderId,
                correlationId = o.CorrelationId,
                strategyName = o.StrategyName,
                strategyVersion = o.StrategyVersion,
                paramsHash = o.ParamsHash,
                mode = o.Mode,
                broker = o.Broker,
                symbol = o.Symbol,
                side = o.Side,
                quantity = o.Quantity,
                orderType = o.OrderType,
                limitPrice = o.LimitPrice,
                stopPrice = o.StopPrice,
                barAtEmitClose = o.BarAtEmitClose,
                barAtEmitTime = o.BarAtEmitTime,
                decisionTrace = decisionTraceJson,
                tag = o.Tag,
                emittedAt,
            }, transaction: tx);

        InsertEventInternal(conn, tx, "order_emitted", orderId, new
        {
            order_id = orderId,
            o.StrategyName,
            o.StrategyVersion,
            o.Symbol,
            o.Side,
            o.Quantity,
            o.Mode,
            o.Broker,
            o.Tag,
        });

        tx.Commit();
        return ReadOne(orderId)!;
    }

    /// <summary>Record a risk decision against an existing order.
    /// One-shot — the schema allows risk_decision to flip from null
    /// to "approve" or "reject" but no further transitions.</summary>
    public void RecordRiskDecision(string orderId, string decision, string? reason)
    {
        if (decision is not ("approve" or "reject"))
            throw new ArgumentException($"risk decision must be approve|reject, got {decision}", nameof(decision));

        using var conn = _db.OpenConnection();
        using var tx = conn.BeginTransaction();
        conn.Execute(@"
            UPDATE orders SET
                risk_decision = @decision,
                risk_reason = @reason,
                risk_decided_at = NOW()
            WHERE order_id = @orderId
              AND risk_decision IS NULL;",
            new { orderId, decision, reason }, transaction: tx);
        InsertEventInternal(conn, tx,
            decision == "approve" ? "order_risk_approved" : "order_risk_rejected",
            orderId,
            new { order_id = orderId, decision, reason });
        tx.Commit();
    }

    /// <summary>Record a fill against an order. Multiple fills per
    /// order allowed (partial fills). Emits a <c>fill_received</c>
    /// event in the same transaction.</summary>
    public long InsertFill(NewFill f)
    {
        using var conn = _db.OpenConnection();
        using var tx = conn.BeginTransaction();

        var fillId = conn.ExecuteScalar<long>(@"
            INSERT INTO fills
                (order_id, broker_order_id, fill_qty, fill_price, commission,
                 filled_at_utc, bar_at_fill_close, bar_at_fill_time, raw_response)
            VALUES
                (@orderId, @brokerOrderId, @fillQty, @fillPrice, @commission,
                 @filledAt, @barAtFillClose, @barAtFillTime, @rawResponse::jsonb)
            RETURNING fill_id;",
            new
            {
                orderId = f.OrderId,
                brokerOrderId = f.BrokerOrderId,
                fillQty = f.FillQty,
                fillPrice = f.FillPrice,
                commission = f.Commission,
                filledAt = f.FilledAtUtc ?? DateTime.UtcNow,
                barAtFillClose = f.BarAtFillClose,
                barAtFillTime = f.BarAtFillTime,
                rawResponse = f.RawResponse is null ? "null" : JsonbHelpers.ToJsonb(f.RawResponse.Value),
            }, transaction: tx);

        InsertEventInternal(conn, tx, "fill_received", f.OrderId, new
        {
            order_id = f.OrderId,
            fill_id = fillId,
            f.FillQty,
            f.FillPrice,
            f.BrokerOrderId,
        });
        tx.Commit();
        return fillId;
    }

    /// <summary>Standalone domain-event write, for events that
    /// don't naturally attach to an order/fill insert (e.g.
    /// regime_shifted, strategy_version_registered).</summary>
    public long InsertEvent(string eventType, string? aggregateId, object payload)
    {
        using var conn = _db.OpenConnection();
        return InsertEventInternal(conn, null, eventType, aggregateId, payload);
    }

    public OrderRecord? ReadOne(string orderId)
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<OrderRow>(@"
            SELECT order_id, correlation_id, strategy_name, strategy_version, params_hash,
                   mode, broker, symbol, side, quantity::float8 AS quantity, order_type,
                   limit_price::float8 AS limit_price, stop_price::float8 AS stop_price,
                   bar_at_emit_close::float8 AS bar_at_emit_close, bar_at_emit_time,
                   decision_trace::text AS decision_trace, tag, emitted_at_utc,
                   risk_decision, risk_reason, risk_decided_at
            FROM orders WHERE order_id = @orderId;",
            new { orderId });
        return row is null ? null : ToRecord(row);
    }

    public IReadOnlyList<OrderRecord> List(int limit = 200, string? symbol = null)
    {
        using var conn = _db.OpenConnection();
        var sql = @"
            SELECT order_id, correlation_id, strategy_name, strategy_version, params_hash,
                   mode, broker, symbol, side, quantity::float8 AS quantity, order_type,
                   limit_price::float8 AS limit_price, stop_price::float8 AS stop_price,
                   bar_at_emit_close::float8 AS bar_at_emit_close, bar_at_emit_time,
                   decision_trace::text AS decision_trace, tag, emitted_at_utc,
                   risk_decision, risk_reason, risk_decided_at
            FROM orders ";
        sql += symbol is null
            ? "ORDER BY emitted_at_utc DESC LIMIT @limit"
            : "WHERE symbol = @symbol ORDER BY emitted_at_utc DESC LIMIT @limit";
        var rows = conn.Query<OrderRow>(sql, new { limit, symbol }).ToList();
        return rows.Select(ToRecord).ToArray();
    }

    public IReadOnlyList<FillRecord> ListFills(string orderId)
    {
        using var conn = _db.OpenConnection();
        return conn.Query<FillRecord>(@"
            SELECT fill_id AS FillId, order_id AS OrderId, broker_order_id AS BrokerOrderId,
                   fill_qty::float8 AS FillQty, fill_price::float8 AS FillPrice,
                   commission::float8 AS Commission, filled_at_utc AS FilledAtUtc,
                   bar_at_fill_close::float8 AS BarAtFillClose, bar_at_fill_time AS BarAtFillTime
            FROM fills WHERE order_id = @orderId
            ORDER BY filled_at_utc ASC;",
            new { orderId }).ToList();
    }

    public IReadOnlyList<EventRecord> ListEvents(int limit = 100, string? eventType = null)
    {
        using var conn = _db.OpenConnection();
        var sql = @"
            SELECT seq, event_type AS EventType, aggregate_id AS AggregateId,
                   payload::text AS PayloadText, occurred_at AS OccurredAt
            FROM events ";
        sql += eventType is null
            ? "ORDER BY seq DESC LIMIT @limit"
            : "WHERE event_type = @eventType ORDER BY seq DESC LIMIT @limit";
        var rows = conn.Query<EventRow>(sql, new { limit, eventType }).ToList();
        return rows.Select(r => new EventRecord(
            Seq: r.seq,
            EventType: r.EventType,
            AggregateId: r.AggregateId,
            Payload: JsonbHelpers.FromJsonb(r.PayloadText),
            OccurredAt: r.OccurredAt)).ToArray();
    }

    private static long InsertEventInternal(
        NpgsqlConnection conn, NpgsqlTransaction? tx,
        string eventType, string? aggregateId, object payload)
    {
        var json = JsonSerializer.Serialize(payload);
        return conn.ExecuteScalar<long>(@"
            INSERT INTO events (event_type, aggregate_id, payload)
            VALUES (@eventType, @aggregateId, @payload::jsonb)
            RETURNING seq;",
            new { eventType, aggregateId, payload = json }, transaction: tx);
    }

    private static OrderRecord ToRecord(OrderRow r) => new(
        OrderId: r.order_id,
        CorrelationId: r.correlation_id,
        StrategyName: r.strategy_name,
        StrategyVersion: r.strategy_version,
        ParamsHash: r.params_hash,
        Mode: r.mode,
        Broker: r.broker,
        Symbol: r.symbol,
        Side: r.side,
        Quantity: r.quantity,
        OrderType: r.order_type,
        LimitPrice: r.limit_price,
        StopPrice: r.stop_price,
        BarAtEmitClose: r.bar_at_emit_close,
        BarAtEmitTime: r.bar_at_emit_time,
        DecisionTrace: JsonbHelpers.FromJsonb(r.decision_trace),
        Tag: r.tag,
        EmittedAtUtc: r.emitted_at_utc,
        RiskDecision: r.risk_decision,
        RiskReason: r.risk_reason,
        RiskDecidedAt: r.risk_decided_at);

    private sealed record OrderRow(
        string order_id, string? correlation_id, string strategy_name, string strategy_version,
        string params_hash, string mode, string broker, string symbol, string side,
        double quantity, string order_type, double? limit_price, double? stop_price,
        double? bar_at_emit_close, DateTime? bar_at_emit_time, string decision_trace,
        string? tag, DateTime emitted_at_utc, string? risk_decision, string? risk_reason,
        DateTime? risk_decided_at);

    private sealed record EventRow(long seq, string EventType, string? AggregateId, string PayloadText, DateTime OccurredAt);
}

/// <summary>Input record for inserting a new order. OrderId can be
/// supplied (when the caller already has a stable id, e.g. a
/// PendingOrder migrating into the event log) or left null for the
/// repository to assign.</summary>
public sealed record NewOrder(
    string StrategyName,
    string StrategyVersion,
    string ParamsHash,
    string Mode,           // backtest | paper_auto | paper_manual | live
    string Broker,
    string Symbol,
    string Side,           // BUY | SELL
    decimal Quantity,
    string OrderType,
    string? OrderId = null,
    string? CorrelationId = null,
    decimal? LimitPrice = null,
    decimal? StopPrice = null,
    decimal? BarAtEmitClose = null,
    DateTime? BarAtEmitTime = null,
    JsonElement? DecisionTrace = null,
    string? Tag = null,
    DateTime? EmittedAtUtc = null);

public sealed record NewFill(
    string OrderId,
    decimal FillQty,
    decimal FillPrice,
    string? BrokerOrderId = null,
    decimal Commission = 0,
    DateTime? FilledAtUtc = null,
    decimal? BarAtFillClose = null,
    DateTime? BarAtFillTime = null,
    JsonElement? RawResponse = null);

public sealed record OrderRecord(
    string OrderId,
    string? CorrelationId,
    string StrategyName,
    string StrategyVersion,
    string ParamsHash,
    string Mode,
    string Broker,
    string Symbol,
    string Side,
    double Quantity,
    string OrderType,
    double? LimitPrice,
    double? StopPrice,
    double? BarAtEmitClose,
    DateTime? BarAtEmitTime,
    JsonElement DecisionTrace,
    string? Tag,
    DateTime EmittedAtUtc,
    string? RiskDecision,
    string? RiskReason,
    DateTime? RiskDecidedAt);

public sealed record FillRecord(
    long FillId,
    string OrderId,
    string? BrokerOrderId,
    double FillQty,
    double FillPrice,
    double Commission,
    DateTime FilledAtUtc,
    double? BarAtFillClose,
    DateTime? BarAtFillTime);

public sealed record EventRecord(
    long Seq,
    string EventType,
    string? AggregateId,
    JsonElement Payload,
    DateTime OccurredAt);
