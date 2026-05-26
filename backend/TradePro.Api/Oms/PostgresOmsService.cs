using Dapper;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using System.Data;
using System.Text.Json;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Oms;

/// <summary>
/// Postgres-backed OmsService. State transitions are wrapped in a
/// transaction with the oms_order_events INSERT so a crash mid-update
/// can't desync the snapshot row from its event log.
///
/// Phase 2 wiring: when ApproveAsync flips a T212_DEMO order to
/// SUBMITTED, it also calls Trading212DemoClient.PlaceMarketOrderAsync
/// inline so the OMS is the single thing that touches the broker.
/// Trading212DemoClient is resolved per-call via IServiceProvider —
/// it's a transient HttpClient and a singleton OmsService can't hold
/// one without leaking its HttpMessageHandler. Tests skip the provider
/// entirely (constructor accepts null) which preserves the Phase 1
/// stub behaviour: state flips, no broker call.
/// </summary>
public sealed class PostgresOmsService : IOmsService
{
    private readonly NpgsqlDataSource _db;
    private readonly IServiceProvider? _services;
    private readonly ILogger<PostgresOmsService> _log;

    public PostgresOmsService(
        NpgsqlDataSource db,
        IServiceProvider? services = null,
        ILogger<PostgresOmsService>? log = null)
    {
        _db = db;
        _services = services;
        _log = log ?? NullLogger<PostgresOmsService>.Instance;
    }

    private Trading212DemoClient? ResolveT212Demo()
    {
        // Per-call scope — Trading212DemoClient is transient, holding
        // it on a singleton would leak HttpMessageHandlers. Returns
        // null in tests (no service provider) so ApproveAsync skips
        // the placement and preserves the Phase 1 stub behaviour.
        if (_services is null) return null;
        using var scope = _services.CreateScope();
        return scope.ServiceProvider.GetService<Trading212DemoClient>();
    }

    public async Task<OmsOrder> EnqueueAsync(OrderIntent intent, string actor)
    {
        await using var conn = await _db.OpenConnectionAsync();
        await using var tx = await conn.BeginTransactionAsync();

        // Idempotency: if a row with the same client_order_id exists,
        // return it untouched. Lets the daemon retry safely without
        // duplicating intents.
        var existingId = await conn.QueryFirstOrDefaultAsync<Guid?>(@"
            SELECT id FROM oms_orders WHERE client_order_id = @cid;",
            new { cid = intent.ClientOrderId }, transaction: tx);
        if (existingId is not null)
        {
            await tx.CommitAsync();
            return (await GetAsync(existingId.Value))!;
        }

        var orderId = await conn.QuerySingleAsync<Guid>(@"
            INSERT INTO oms_orders (
                client_order_id, broker, strategy_id, symbol, side, qty,
                order_type, limit_price, stop_price, time_in_force,
                placed_by, state
            )
            VALUES (
                @ClientOrderId, @Broker, @StrategyId, @Symbol, @Side, @Qty,
                @OrderType, @LimitPrice, @StopPrice, @TimeInForce,
                @PlacedBy, 'PENDING_APPROVAL'
            )
            RETURNING id;",
            intent, transaction: tx);

        await InsertEventAsync(conn, tx, orderId,
            eventType: "ENQUEUED",
            priorState: null,
            newState: OmsState.PendingApproval,
            actor: actor,
            detail: null);

        await tx.CommitAsync();
        return (await GetAsync(orderId))!;
    }

    public async Task<OmsOrder?> GetAsync(Guid orderId)
    {
        await using var conn = await _db.OpenConnectionAsync();
        return await ReadOneAsync(conn, transaction: null, orderId);
    }

    public async Task<IReadOnlyList<OmsOrder>> ListAsync(
        IReadOnlyCollection<string>? states, int limit)
    {
        await using var conn = await _db.OpenConnectionAsync();
        var (whereSql, args) = (states is { Count: > 0 })
            ? ("WHERE state = ANY(@states)", (object)new { states = states.ToArray() })
            : ("", new { });
        var sql = $@"
            SELECT
                id              AS Id,
                client_order_id AS ClientOrderId,
                broker, broker_order_id AS BrokerOrderId,
                strategy_id     AS StrategyId,
                symbol, side, qty,
                order_type      AS OrderType,
                limit_price     AS LimitPrice,
                stop_price      AS StopPrice,
                time_in_force   AS TimeInForce,
                state,
                placed_by       AS PlacedBy,
                filled_qty      AS FilledQty,
                avg_fill_price  AS AvgFillPrice,
                cancelled_reason AS CancelledReason,
                created_at_utc  AS CreatedAtUtc,
                last_state_change_at_utc AS LastStateChangeAtUtc
            FROM oms_orders
            {whereSql}
            ORDER BY created_at_utc DESC
            LIMIT {limit};";
        var rows = await conn.QueryAsync<OmsOrder>(sql, args);
        return rows.ToList();
    }

    public async Task<OmsOrder> ApproveAsync(Guid orderId, string actor)
    {
        // 1. Flip state to SUBMITTED + write the APPROVED event. State-
        //    machine guard inside TransitionAsync rejects approve from
        //    any non-PENDING_APPROVAL row.
        var approved = await TransitionAsync(
            orderId, actor,
            allowedPriorStates: new[] { OmsState.PendingApproval },
            newState: OmsState.Submitted,
            eventType: "APPROVED",
            extraSetSql: null,
            extraParams: null,
            cancelledReason: null);

        // 2. If this is a T212_DEMO order AND the demo client is wired,
        //    place against the broker inline. Failure → roll the order
        //    to REJECTED with the T212 error in cancelled_reason so the
        //    operator sees WHY it didn't make it to the broker on the
        //    /oms page instead of an opaque SUBMITTED-stuck state.
        //    Other brokers (PAPER, IBKR, T212_LIVE) skip placement —
        //    PAPER fills via the engine's PaperOrderRouter; IBKR + LIVE
        //    plug in here in later phases.
        var t212Demo = ResolveT212Demo();
        if (approved.Broker == "T212_DEMO" && t212Demo is not null)
        {
            var signedQty = approved.Side == "BUY"
                ? Math.Abs(approved.Qty)
                : -Math.Abs(approved.Qty);
            try
            {
                var result = await t212Demo.PlaceMarketOrderAsync(
                    approved.Symbol, signedQty, CancellationToken.None);

                if (!string.IsNullOrEmpty(result.Error))
                {
                    _log.LogWarning(
                        "T212 demo placement failed for OMS order {OrderId} ({Sym} {Side} {Qty}): {Error}",
                        approved.Id, approved.Symbol, approved.Side, approved.Qty, result.Error);
                    // Use the existing transition path so the event log
                    // captures the rejection symmetrically with the
                    // happy path.
                    return await TransitionAsync(
                        approved.Id, actor: "broker:T212_DEMO",
                        allowedPriorStates: new[] { OmsState.Submitted },
                        newState: OmsState.Rejected,
                        eventType: "BROKER_REJECTED",
                        extraSetSql: "cancelled_reason = @rejReason,",
                        extraParams: new { rejReason = result.Error },
                        cancelledReason: result.Error,
                        detail: new { httpStatus = result.HttpStatus, body = result.ResponseBody });
                }

                // Success — record broker_order_id so future events
                // (fills, broker-side cancels) can join back.
                if (result.OrderId is long brokerId)
                {
                    await using var conn = await _db.OpenConnectionAsync();
                    await conn.ExecuteAsync(@"
                        UPDATE oms_orders
                        SET broker_order_id = @bid
                        WHERE id = @oid;",
                        new { bid = brokerId.ToString(), oid = approved.Id });
                }
                return (await GetAsync(approved.Id))!;
            }
            catch (Exception ex)
            {
                _log.LogError(ex,
                    "T212 demo placement threw for OMS order {OrderId} — leaving SUBMITTED for operator review",
                    approved.Id);
                // Keep SUBMITTED state — transient T212 error shouldn't
                // permanently kill the order. Operator can manually
                // Cancel via /oms if needed.
                return approved;
            }
        }
        return approved;
    }

    public async Task<OmsOrder> RejectAsync(Guid orderId, string actor, string reason) =>
        await TransitionAsync(
            orderId, actor,
            allowedPriorStates: new[] { OmsState.PendingApproval },
            newState: OmsState.Rejected,
            eventType: "REJECTED",
            extraSetSql: null,
            extraParams: null,
            cancelledReason: null,
            detail: new { reason });

    public async Task<OmsOrder> CancelAsync(Guid orderId, string actor, string reason) =>
        await TransitionAsync(
            orderId, actor,
            allowedPriorStates: OmsState.OpenStates,
            newState: OmsState.Cancelled,
            eventType: "CANCELLED",
            extraSetSql: "cancelled_reason = @reason,",
            extraParams: new { reason },
            cancelledReason: reason);

    public async Task<IReadOnlyList<Guid>> CancelAllOpenAsync(string actor, string reason)
    {
        await using var conn = await _db.OpenConnectionAsync();
        var openIds = (await conn.QueryAsync<Guid>(@"
            SELECT id FROM oms_orders WHERE state = ANY(@open);",
            new { open = OmsState.OpenStates })).ToList();
        foreach (var id in openIds)
        {
            await CancelAsync(id, actor, reason);
        }
        return openIds;
    }

    public async Task<OmsOrder> RecordFillAsync(
        Guid orderId, decimal qty, decimal price, decimal fee, string currency,
        string? brokerFillId, string actor)
    {
        if (qty <= 0)
            throw new ArgumentException("fill qty must be > 0", nameof(qty));

        await using var conn = await _db.OpenConnectionAsync();
        await using var tx = await conn.BeginTransactionAsync();

        // Lock the parent row so concurrent fills don't desync
        // filled_qty / avg_fill_price.
        var parent = await conn.QueryFirstOrDefaultAsync<OmsOrder>(@"
            SELECT
                id              AS Id,
                client_order_id AS ClientOrderId,
                broker, broker_order_id AS BrokerOrderId,
                strategy_id     AS StrategyId,
                symbol, side, qty,
                order_type      AS OrderType,
                limit_price     AS LimitPrice,
                stop_price      AS StopPrice,
                time_in_force   AS TimeInForce,
                state,
                placed_by       AS PlacedBy,
                filled_qty      AS FilledQty,
                avg_fill_price  AS AvgFillPrice,
                cancelled_reason AS CancelledReason,
                created_at_utc  AS CreatedAtUtc,
                last_state_change_at_utc AS LastStateChangeAtUtc
            FROM oms_orders
            WHERE id = @orderId FOR UPDATE;",
            new { orderId }, transaction: tx);
        if (parent is null)
            throw new InvalidOperationException($"order {orderId} not found");
        if (!OmsState.OpenStates.Contains(parent.State) && parent.State != OmsState.Filled)
            throw new InvalidOperationException(
                $"cannot fill terminal order in state {parent.State}");

        await conn.ExecuteAsync(@"
            INSERT INTO oms_fills
              (order_id, broker_fill_id, qty, price, fee, currency)
            VALUES
              (@orderId, @brokerFillId, @qty, @price, @fee, @currency);",
            new { orderId, brokerFillId, qty, price, fee, currency },
            transaction: tx);

        var newFilledQty = parent.FilledQty + qty;
        // Weighted avg over all fills so far.
        var newAvg = parent.AvgFillPrice.HasValue
            ? ((parent.AvgFillPrice.Value * parent.FilledQty) + (price * qty)) / newFilledQty
            : price;
        var fullyFilled = newFilledQty >= parent.Qty;
        var priorState = parent.State;
        var newState = fullyFilled ? OmsState.Filled : OmsState.PartiallyFilled;

        await conn.ExecuteAsync(@"
            UPDATE oms_orders
            SET filled_qty = @newFilledQty,
                avg_fill_price = @newAvg,
                state = @newState,
                last_state_change_at_utc = NOW()
            WHERE id = @orderId;",
            new { orderId, newFilledQty, newAvg, newState }, transaction: tx);

        await InsertEventAsync(conn, tx, orderId,
            eventType: "FILL",
            priorState: priorState,
            newState: newState,
            actor: actor,
            detail: new { qty, price, fee, currency, brokerFillId });

        await tx.CommitAsync();
        return (await GetAsync(orderId))!;
    }

    public async Task<IReadOnlyList<OmsPosition>> ListPositionsAsync(string? strategyId)
    {
        await using var conn = await _db.OpenConnectionAsync();
        // Sign-flip the SELL fills so the SUM yields signed net qty.
        // Weighted avg = SUM(qty * price) / SUM(qty) on the absolute
        // qty so a zero-net position doesn't divide by zero.
        var (where, args) = strategyId is null
            ? ("WHERE o.strategy_id IS NOT NULL", (object)new { })
            : ("WHERE o.strategy_id = @sid", (object)new { sid = strategyId });
        var rows = await conn.QueryAsync<OmsPosition>($@"
            SELECT
                o.strategy_id      AS StrategyId,
                o.symbol           AS Symbol,
                o.broker           AS Broker,
                SUM(CASE WHEN o.side = 'BUY' THEN f.qty ELSE -f.qty END) AS Quantity,
                CASE WHEN SUM(f.qty) = 0 THEN NULL
                     ELSE SUM(f.qty * f.price) / SUM(f.qty)
                END                AS AvgPrice,
                MAX(f.fill_at_utc) AS LastFillAtUtc
            FROM oms_orders o
            JOIN oms_fills f ON f.order_id = o.id
            {where}
            GROUP BY o.strategy_id, o.symbol, o.broker
            HAVING SUM(CASE WHEN o.side = 'BUY' THEN f.qty ELSE -f.qty END) <> 0
            ORDER BY o.strategy_id, o.symbol;",
            args);
        return rows.ToList();
    }

    public async Task<IReadOnlyList<OmsOrderEvent>> ListEventsAsync(Guid orderId)
    {
        await using var conn = await _db.OpenConnectionAsync();
        var rows = await conn.QueryAsync<OmsOrderEvent>(@"
            SELECT
                id,
                order_id        AS OrderId,
                event_type      AS EventType,
                prior_state     AS PriorState,
                new_state       AS NewState,
                actor,
                detail::text    AS DetailJson,
                occurred_at_utc AS OccurredAtUtc
            FROM oms_order_events
            WHERE order_id = @orderId
            ORDER BY occurred_at_utc ASC, id ASC;",
            new { orderId });
        return rows.ToList();
    }

    // ── internal helpers ──────────────────────────────────────────

    private async Task<OmsOrder> TransitionAsync(
        Guid orderId,
        string actor,
        IReadOnlyCollection<string> allowedPriorStates,
        string newState,
        string eventType,
        string? extraSetSql,
        object? extraParams,
        string? cancelledReason,
        object? detail = null)
    {
        await using var conn = await _db.OpenConnectionAsync();
        await using var tx = await conn.BeginTransactionAsync();

        var priorState = await conn.QueryFirstOrDefaultAsync<string?>(@"
            SELECT state FROM oms_orders WHERE id = @orderId FOR UPDATE;",
            new { orderId }, transaction: tx);
        if (priorState is null)
            throw new InvalidOperationException($"order {orderId} not found");
        if (!allowedPriorStates.Contains(priorState))
            throw new InvalidOperationException(
                $"cannot {eventType.ToLower()} order in state {priorState}; expected one of {string.Join(",", allowedPriorStates)}");

        var setExtra = extraSetSql ?? "";
        var sql = $@"
            UPDATE oms_orders
            SET state = @newState,
                {setExtra}
                last_state_change_at_utc = NOW()
            WHERE id = @orderId;";
        var args = new DynamicParameters();
        args.Add("orderId", orderId);
        args.Add("newState", newState);
        if (extraParams is not null)
        {
            // Merge any extra named params into the same dynamic set
            // (avoids one-shot anonymous-type combinatorics).
            foreach (var p in extraParams.GetType().GetProperties())
                args.Add(p.Name, p.GetValue(extraParams));
        }
        await conn.ExecuteAsync(sql, args, transaction: tx);

        await InsertEventAsync(conn, tx, orderId,
            eventType: eventType,
            priorState: priorState,
            newState: newState,
            actor: actor,
            detail: detail);

        await tx.CommitAsync();
        return (await GetAsync(orderId))!;
    }

    private static async Task<OmsOrder?> ReadOneAsync(
        NpgsqlConnection conn, IDbTransaction? transaction, Guid orderId)
    {
        return await conn.QueryFirstOrDefaultAsync<OmsOrder>(@"
            SELECT
                id              AS Id,
                client_order_id AS ClientOrderId,
                broker, broker_order_id AS BrokerOrderId,
                strategy_id     AS StrategyId,
                symbol, side, qty,
                order_type      AS OrderType,
                limit_price     AS LimitPrice,
                stop_price      AS StopPrice,
                time_in_force   AS TimeInForce,
                state,
                placed_by       AS PlacedBy,
                filled_qty      AS FilledQty,
                avg_fill_price  AS AvgFillPrice,
                cancelled_reason AS CancelledReason,
                created_at_utc  AS CreatedAtUtc,
                last_state_change_at_utc AS LastStateChangeAtUtc
            FROM oms_orders
            WHERE id = @orderId;",
            new { orderId }, transaction: transaction);
    }

    private static async Task InsertEventAsync(
        NpgsqlConnection conn, IDbTransaction tx, Guid orderId,
        string eventType, string? priorState, string newState,
        string actor, object? detail)
    {
        var detailJson = detail is null ? null : JsonSerializer.Serialize(detail);
        await conn.ExecuteAsync(@"
            INSERT INTO oms_order_events
              (order_id, event_type, prior_state, new_state, actor, detail)
            VALUES
              (@orderId, @eventType, @priorState, @newState, @actor, @detailJson::jsonb);",
            new { orderId, eventType, priorState, newState, actor, detailJson },
            transaction: tx);
    }
}
