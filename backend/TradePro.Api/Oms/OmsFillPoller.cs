using Dapper;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Oms;

/// <summary>
/// Background poller that closes the SUBMITTED → FILLED loop on T212
/// demo. T212 has no public webhook / SSE for order updates, so we
/// poll GET /equity/orders/{id} every N seconds for every order OMS
/// thinks is still in flight and reconcile state.
///
/// Without this, OmsService.ApproveAsync flips an order to SUBMITTED
/// (the broker call returns an id) but OMS never learns when T212
/// actually fills it. Operator has to alt-tab to T212's app to know.
///
/// Pacing: T212 enforces 1 req/sec per endpoint. We sleep 1.1s
/// between order polls inside a tick to stay under that ceiling
/// even when there are many open orders. Tick interval defaults to
/// 30s but is configurable via Oms:PollSeconds.
/// </summary>
public sealed class OmsFillPoller : BackgroundService
{
    private static readonly string[] InFlightStates =
    {
        OmsState.Submitted,
        OmsState.Working,
        OmsState.PartiallyFilled,
    };

    private readonly NpgsqlDataSource _db;
    private readonly IServiceProvider _services;
    private readonly ILogger<OmsFillPoller> _log;
    private readonly TimeSpan _tickInterval;
    private readonly TimeSpan _perOrderDelay;

    public OmsFillPoller(
        NpgsqlDataSource db,
        IServiceProvider services,
        IConfiguration config,
        ILogger<OmsFillPoller> log)
    {
        _db = db;
        _services = services;
        _log = log;
        var secs = config.GetValue<int?>("Oms:PollSeconds") ?? 30;
        _tickInterval = TimeSpan.FromSeconds(Math.Clamp(secs, 5, 600));
        _perOrderDelay = TimeSpan.FromMilliseconds(1100);
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation(
            "OmsFillPoller started: tick={Tick}s perOrderDelay={Delay}ms",
            _tickInterval.TotalSeconds, _perOrderDelay.TotalMilliseconds);
        // Start with a small delay so the app finishes initialising
        // (Postgres migrations, DI graph) before we start hitting
        // T212.
        try { await Task.Delay(TimeSpan.FromSeconds(10), stoppingToken); }
        catch (OperationCanceledException) { return; }

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await PollTickAsync(stoppingToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogError(ex, "OmsFillPoller tick threw");
            }
            try { await Task.Delay(_tickInterval, stoppingToken); }
            catch (OperationCanceledException) { return; }
        }
    }

    private async Task PollTickAsync(CancellationToken ct)
    {
        // 1. Find in-flight T212_DEMO orders with a broker_order_id
        //    (no broker id means the placement call hasn't completed
        //    or failed; nothing to poll). Cap to 50 per tick so a
        //    runaway queue doesn't starve the poller.
        await using var conn = await _db.OpenConnectionAsync(ct);
        var rows = (await conn.QueryAsync<(Guid OrderId, string BrokerId, decimal Qty)>(@"
            SELECT id           AS OrderId,
                   broker_order_id AS BrokerId,
                   qty           AS Qty
            FROM oms_orders
            WHERE broker = 'T212_DEMO'
              AND state = ANY(@states)
              AND broker_order_id IS NOT NULL
            ORDER BY created_at_utc ASC
            LIMIT 50;",
            new { states = InFlightStates })).ToList();

        if (rows.Count == 0) return;

        _log.LogDebug("OmsFillPoller: polling {Count} in-flight T212 demo orders", rows.Count);

        // 2. Resolve fresh-scoped Trading212DemoClient + OmsService
        //    via DI. Background service is singleton; HttpClient must
        //    be transient.
        using var scope = _services.CreateScope();
        var demo = scope.ServiceProvider.GetService<Trading212DemoClient>();
        var oms = scope.ServiceProvider.GetRequiredService<IOmsService>();
        if (demo is null || !demo.IsEnabled)
        {
            _log.LogDebug("OmsFillPoller: Trading212DemoClient unavailable; skip tick");
            return;
        }

        // 3. Poll each one. Sleep between to respect T212's
        //    1 req/sec per endpoint rate limit.
        foreach (var (orderId, brokerIdRaw, declaredQty) in rows)
        {
            if (ct.IsCancellationRequested) return;
            if (!long.TryParse(brokerIdRaw, out var brokerId))
            {
                _log.LogWarning(
                    "OmsFillPoller: broker_order_id {Raw} on {OrderId} is not a long; skip",
                    brokerIdRaw, orderId);
                continue;
            }
            try
            {
                var status = await demo.GetOrderStatusAsync(brokerId, ct);
                if (status is null) continue;
                await ReconcileAsync(oms, orderId, declaredQty, status, ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogWarning(ex,
                    "OmsFillPoller: reconcile failed for {OrderId} brokerId={BrokerId}",
                    orderId, brokerId);
            }
            try { await Task.Delay(_perOrderDelay, ct); }
            catch (OperationCanceledException) { return; }
        }
    }

    private async Task ReconcileAsync(
        IOmsService oms,
        Guid orderId,
        decimal declaredQty,
        Trading212OrderStatus status,
        CancellationToken ct)
    {
        var s = (status.Status ?? "").ToUpperInvariant();
        switch (s)
        {
            case "FILLED":
                {
                    // Record the fill if not already recorded. avg
                    // price = filledValue / filledQuantity when both
                    // are present; otherwise leave price 0 and let
                    // operator reconcile manually later.
                    var qty = status.FilledQuantity ?? declaredQty;
                    var avg = (status.FilledValue is decimal v
                        && status.FilledQuantity is decimal q
                        && q > 0) ? v / q : 0m;
                    await oms.RecordFillAsync(
                        orderId, qty: qty, price: avg, fee: 0m,
                        currency: "USD",
                        brokerFillId: status.BrokerOrderId.ToString(),
                        actor: "poller:T212_DEMO");
                    _log.LogInformation(
                        "OmsFillPoller: order {OrderId} FILLED qty={Qty} avg={Avg}",
                        orderId, qty, avg);
                    break;
                }
            case "CANCELLED":
                {
                    await oms.CancelAsync(orderId, "poller:T212_DEMO", "broker_cancelled");
                    _log.LogInformation("OmsFillPoller: order {OrderId} CANCELLED at broker", orderId);
                    break;
                }
            case "REJECTED":
                {
                    // Reject after Submission is a state we don't have
                    // a direct transition for in the public service
                    // interface. Fall back to Cancel with the rejection
                    // reason in cancelled_reason — the OMS event log
                    // will carry the broker-side payload.
                    await oms.CancelAsync(orderId, "poller:T212_DEMO", "broker_rejected");
                    _log.LogInformation("OmsFillPoller: order {OrderId} REJECTED at broker", orderId);
                    break;
                }
            case "GONE":
                {
                    // 404 from T212 + history miss. Empirically: when
                    // T212 issues a broker_order_id, the order has been
                    // ACCEPTED. Rejections happen at placement (before
                    // the id is returned, with HTTP 4xx). So an order
                    // that aged out of /orders/{id} hot cache AND isn't
                    // in /history is overwhelmingly a FILLED order, not
                    // a cancelled one.
                    //
                    // The broker is the golden source — verified
                    // empirically by the NVDA SELL case:
                    // T212 position 6.7022 → 0.7022 confirmed the fill
                    // even though our poller marked CANCELLED.
                    // (project_broker_is_golden_source).
                    //
                    // Mark FILLED with avg=0; the operator can reconcile
                    // the fill price via T212's UI if they care about
                    // realised P&L attribution. The position-tracking
                    // side is correct because the broker holds the truth.
                    await oms.RecordFillAsync(
                        orderId, qty: declaredQty, price: 0m, fee: 0m,
                        currency: "USD",
                        brokerFillId: $"assumed_via_404:{status.BrokerOrderId}",
                        actor: "poller:T212_DEMO");
                    _log.LogInformation(
                        "OmsFillPoller: order {OrderId} FILLED (assumed via T212 hot-cache 404 + history miss)",
                        orderId);
                    break;
                }
            // NEW / WORKING / PARTIAL — still in flight, no transition.
            // We'll see it again next tick.
        }
    }
}
