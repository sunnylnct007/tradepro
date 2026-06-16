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
    private readonly TimeSpan _unacceptedGrace;

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
        // Grace before an in-flight order with no broker_order_id is
        // treated as never-accepted-by-the-broker and expired. A real
        // dispatch assigns the broker id synchronously inside ApproveAsync,
        // so this only needs to outlast a momentary blip. Default 3 min.
        var graceSecs = config.GetValue<int?>("Oms:UnacceptedGraceSeconds") ?? 180;
        _unacceptedGrace = TimeSpan.FromSeconds(Math.Clamp(graceSecs, 30, 3600));
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
        // 0. Reconcile ZOMBIE orders against the broker (golden source):
        //    any in-flight order with no broker_order_id that has sat past
        //    the grace window. ApproveAsync assigns the broker id
        //    synchronously on a successful dispatch, so a NULL after
        //    minutes means placement never completed — the IG/T212 client
        //    threw or was disabled at approve time and the order was "left
        //    SUBMITTED". The broker has NO deal for it, so it must not keep
        //    showing as in-flight ("N waiting" on the cockpit) when the
        //    book is actually flat. This is broker-agnostic and runs even
        //    when T212 is unavailable, so IG zombies get cleaned too (the
        //    T212-only fill poll below never touched them).
        using (var sweepScope = _services.CreateScope())
        {
            var sweepOms = sweepScope.ServiceProvider.GetRequiredService<IOmsService>();
            try
            {
                var expired = await sweepOms.ExpireUnacceptedAsync(_unacceptedGrace, "poller:reconcile");
                if (expired.Count > 0)
                    _log.LogInformation(
                        "OmsFillPoller: expired {Count} zombie order(s) the broker never accepted "
                        + "(no broker_order_id, stale > {Grace}s)", expired.Count, _unacceptedGrace.TotalSeconds);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogError(ex, "OmsFillPoller: zombie-expiry sweep threw");
            }
        }

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

        // 4. Backfill sweep — FILLED T212 orders recorded at price 0 because
        //    T212 aged the order out of its hot cache before the poller could
        //    capture the price. Re-look-up each in /equity/history/orders
        //    (which carries the direct fillPrice) and patch avg_fill_price
        //    WITHOUT touching qty/state. Bounded to recent orders (history
        //    only retains the last ~250) + LIMIT 10/tick + the same 1.1s
        //    pacing, so it chips away without tripping T212's rate limit.
        //    Successful rows leave the set (price no longer 0); only orders
        //    genuinely missing from history are re-tried.
        var zeroPriced = (await conn.QueryAsync<(Guid OrderId, string BrokerId)>(@"
            SELECT id AS OrderId, broker_order_id AS BrokerId
            FROM oms_orders
            WHERE broker = 'T212_DEMO'
              AND state IN ('FILLED', 'PARTIALLY_FILLED')
              AND broker_order_id IS NOT NULL
              AND (avg_fill_price IS NULL OR avg_fill_price = 0)
              AND created_at_utc > NOW() - INTERVAL '3 days'
            ORDER BY created_at_utc DESC
            LIMIT 10;")).ToList();
        if (zeroPriced.Count > 0)
            _log.LogInformation(
                "OmsFillPoller: backfill sweep — {Count} FILLED T212 order(s) at price 0 to reconcile",
                zeroPriced.Count);
        foreach (var (orderId, brokerIdRaw) in zeroPriced)
        {
            if (ct.IsCancellationRequested) return;
            if (!long.TryParse(brokerIdRaw, out var brokerId)) continue;
            try
            {
                var status = await demo.GetOrderStatusAsync(brokerId, ct);
                if (status is null) continue;
                var price = status.FillPrice is decimal fp && fp > 0m
                    ? fp
                    : (status.FilledValue is decimal v
                        && status.FilledQuantity is decimal q
                        && q > 0) ? v / q : 0m;
                if (price > 0m)
                {
                    var ok = await oms.BackfillFillPriceAsync(
                        orderId, price, "poller:T212_DEMO:backfill");
                    if (ok)
                        _log.LogInformation(
                            "OmsFillPoller: backfilled fill price {Price} for order {OrderId} "
                            + "from T212 history", price, orderId);
                }
                else
                {
                    _log.LogDebug(
                        "OmsFillPoller: order {OrderId} still has no price in T212 history "
                        + "(too old / aged past the ~250-order window) — leaving at 0", orderId);
                }
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogWarning(ex,
                    "OmsFillPoller: backfill lookup failed for {OrderId}", orderId);
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
                    // Record the fill. Price preference:
                    //   1. T212's direct fillPrice (history endpoint) — canonical
                    //   2. filledValue / filledQuantity (live endpoint)
                    //   3. 0 as last resort (the backfill sweep retries from history)
                    var qty = status.FilledQuantity ?? declaredQty;
                    var avg = status.FillPrice is decimal fp && fp > 0m
                        ? fp
                        : (status.FilledValue is decimal v
                            && status.FilledQuantity is decimal q
                            && q > 0) ? v / q : 0m;
                    await oms.RecordFillAsync(
                        orderId, qty: qty, price: avg, fee: 0m,
                        currency: "USD",
                        brokerFillId: status.BrokerOrderId.ToString(),
                        actor: "poller:T212_DEMO");
                    if (avg == 0m)
                        _log.LogWarning(
                            "OmsFillPoller: order {OrderId} FILLED but T212 gave no usable "
                            + "price (fillPrice={Fp} filledValue={Fv} filledQty={Fq}) — recorded "
                            + "0, backfill sweep will retry from history",
                            orderId, status.FillPrice, status.FilledValue, status.FilledQuantity);
                    else
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
