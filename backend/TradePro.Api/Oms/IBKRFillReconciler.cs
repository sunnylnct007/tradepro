using TradePro.Api.Providers.IBKR;

namespace TradePro.Api.Oms;

/// <summary>Outcome of one IBKR fill-reconcile pass. <paramref name="Payload"/>
/// is the same anonymous shape the endpoint has always returned.</summary>
public sealed record IBKRFillReconcileResult(bool Ok, string? Error, object? Payload);

/// <summary>
/// Joins IBKR executions to OMS orders and records the fills.
///
/// EXTRACTED 27 Aug 2026, the day the read was fixed. This logic lived inline
/// in the POST /integrations/ibkr/reconcile-oms handler, which meant it ran
/// ONLY when a human called that endpoint. GoldenSourceReconciler ticks every
/// 60s but reconciles POSITIONS, never orders or executions -- so the whole
/// fill-recording path had no scheduled caller at all.
///
/// The consequence, had this stayed: today's fixes would have worked every time
/// they were tested by hand and never once on their own. A Swing order would
/// fill at the broker and sit in the OMS as SUBMITTED with a null price, which
/// is indistinguishable from the two-month bug that was just closed.
///
/// One implementation, two callers -- the endpoint and IBKRFillReconcileService.
/// Duplicating it instead would have been the failure mode this codebase hits
/// most: two copies that quietly disagree.
/// </summary>
public sealed class IBKRFillReconciler
{
    private readonly IBKRClient _ibkr;
    private readonly IOmsService _oms;
    private readonly ILogger<IBKRFillReconciler> _log;

    public IBKRFillReconciler(IBKRClient ibkr, IOmsService oms, ILogger<IBKRFillReconciler> log)
    { _ibkr = ibkr; _oms = oms; _log = log; }

    public async Task<IBKRFillReconcileResult> RunAsync(CancellationToken ct)
    {
        var ibkr = _ibkr;
        var oms = _oms;
        var log = _log;
        if (!ibkr.IsEnabled)
            return new IBKRFillReconcileResult(false, "IBKR disabled", null);
        var res = await ibkr.GetLiveOrdersAsync(ct);
        if (res.Error is not null)
            return new IBKRFillReconcileResult(false, $"broker order fetch failed: {res.Error}", null);

        var byId = new Dictionary<string, TradePro.Api.Providers.IBKR.IBKRLiveOrder>(StringComparer.OrdinalIgnoreCase);
        foreach (var bo in res.Orders)
            if (!string.IsNullOrWhiteSpace(bo.OrderId)) byId[bo.OrderId!] = bo;

        // EXECUTIONS ARE THE SOURCE OF THE PRICE (25 Aug 2026).
        //
        // The orders blotter has been returning ZERO rows for this account
        // since at least 29 July. Every open OMS order was therefore
        // reported "no-broker-match (aged out of blotter)" — including a
        // probe order placed seventy minutes earlier — while the response
        // carried appliedCount:7 and read like success. The consequence:
        // six orders recorded FILLED at a price of ZERO and nine stuck in
        // SUBMITTED for weeks, which makes forward-test gates F2, F3 and
        // F4 uncomputable.
        //
        // /iserver/account/trades DOES return data — the ledger path has
        // been using it all along ("recorded 2 IBKR_PAPER execution(s)").
        // Executions carry the actual FILL PRICE, and IBKR returns the
        // owning order id on them; the parser was simply dropping it. So
        // reconcile from executions FIRST and fall back to the blotter.
        var execs = await ibkr.GetTradesAsync(ct);
        var fillsByOrder = new Dictionary<string, (decimal Qty, decimal Notional, string? ExecId)>(
            StringComparer.OrdinalIgnoreCase);
        foreach (var tr in execs.Trades)
        {
            if (string.IsNullOrWhiteSpace(tr.OrderId) || tr.Size <= 0m || tr.Price <= 0m) continue;
            fillsByOrder.TryGetValue(tr.OrderId!, out var acc);
            fillsByOrder[tr.OrderId!] =
                (acc.Qty + tr.Size, acc.Notional + (tr.Size * tr.Price), tr.ExecId ?? acc.ExecId);
        }

        var open = (await oms.ListAsync(new[] { "SUBMITTED", "WORKING", "PARTIALLY_FILLED" }, 500))
            .Where(o => (o.Broker is "IBKR_PAPER" or "IBKR_LIVE") && !string.IsNullOrWhiteSpace(o.BrokerOrderId))
            .ToList();

        var applied = new List<object>();
        foreach (var o in open)
        {
            // Executions first — they carry a real price.
            if (fillsByOrder.TryGetValue(o.BrokerOrderId!, out var xf) && xf.Qty > 0m)
            {
                var px = xf.Notional / xf.Qty;
                var delta = xf.Qty - o.FilledQty;
                if (delta > 0m && px > 0m)
                {
                    await oms.RecordFillAsync(o.Id, delta, px, 0m, "USD",
                        xf.ExecId ?? $"exec:{o.BrokerOrderId}", "broker:executions");
                    applied.Add(new { o.Symbol, o.BrokerOrderId,
                                      action = "FILLED from executions", qty = delta, price = px });
                    continue;
                }
            }
            if (!byId.TryGetValue(o.BrokerOrderId!, out var bo))
            {
                // An EMPTY blotter is a different fact from an order too
                // old to appear in a populated one, and reporting them
                // identically is exactly what hid this for four weeks.
                applied.Add(new {
                    o.Symbol, o.BrokerOrderId,
                    action = res.Orders.Count == 0
                        ? "UNRESOLVED — broker returned an EMPTY order blotter and no execution "
                          + "matches this order id; the OMS cannot confirm this order either way"
                        : "no-broker-match (aged out of blotter)",
                });
                continue;
            }
            var status = (bo.Status ?? "").ToLowerInvariant();
            try
            {
                if (status.Contains("fill"))
                {
                    var totalFilled = bo.FilledQty ?? bo.TotalSize ?? o.Qty;
                    var delta = totalFilled - o.FilledQty;   // record only the NEW fill (idempotent)
                    var px = bo.AvgPrice ?? 0m;
                    if (delta > 0m && px > 0m)
                    {
                        await oms.RecordFillAsync(o.Id, delta, px, 0m, "USD", $"recon:{o.BrokerOrderId}", "broker:reconcile");
                        applied.Add(new { o.Symbol, o.BrokerOrderId, action = "FILLED from broker", qty = delta, price = px });
                    }
                    else
                    {
                        applied.Add(new { o.Symbol, o.BrokerOrderId, action = "broker Filled — no new qty/price yet" });
                    }
                }
                else if (status.Contains("cancel"))
                {
                    await oms.CancelAsync(o.Id, "broker:reconcile", "cancelled at broker");
                    applied.Add(new { o.Symbol, o.BrokerOrderId, action = "CANCELLED from broker" });
                }
                // else still working at the broker → leave the OMS as-is
            }
            catch (Exception ex)
            {
                log.LogWarning(ex, "reconcile failed for OMS {Id} / broker {Bid}", o.Id, o.BrokerOrderId);
                applied.Add(new { o.Symbol, o.BrokerOrderId, action = $"reconcile error: {ex.Message}" });
            }
        }
        // appliedCount USED TO READ LIKE SUCCESS while nothing was
        // confirmed: seven orders "applied", every one of them the
        // no-match branch, against an empty blotter. Report what actually
        // happened — confirmed vs unresolved — and say plainly when BOTH
        // broker reads came back empty, because that is a broker-side
        // outage and not a quiet day.
        var confirmed = applied.Count(a =>
            a.GetType().GetProperty("action")?.GetValue(a) as string is string s2
            && (s2.StartsWith("FILLED") || s2.StartsWith("CANCELLED")));
        var blind = res.Orders.Count == 0 && fillsByOrder.Count == 0;
        if (blind && open.Count > 0)
            log.LogError(
                "IBKR reconcile is BLIND — the orders blotter returned 0 rows AND no executions "
                + "carried an order id, with {Open} OMS order(s) open. No fill can be confirmed, "
                + "so none of them can be graded. This is a broker READ failure, not an absence "
                + "of trading.", open.Count);
        return new IBKRFillReconcileResult(true, null, new
        {
            brokerOrders = res.Orders.Count,
            brokerExecutions = execs.Trades.Count,
            executionsWithOrderId = fillsByOrder.Count,
            omsOpen = open.Count,
            confirmed,
            unresolved = applied.Count - confirmed,
            blind,
            // WHY the reads were empty. Both client methods catch their own
            // exceptions and return an EMPTY result carrying the message, so
            // without these fields a THROWN read and a genuinely empty account
            // are the same response -- zero rows, no error. That ambiguity is
            // what made "the blotter is empty" look like a fact for weeks. An
            // httpStatus of 0 means the call never completed at all.
            ordersError = res.Error,
            ordersHttpStatus = res.HttpStatus,
            executionsError = execs.Error,
            executionsHttpStatus = execs.HttpStatus,
            applied,
        });
    }
}
