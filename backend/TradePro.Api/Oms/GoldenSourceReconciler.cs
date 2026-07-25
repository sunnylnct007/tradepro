using Microsoft.Extensions.Hosting;

namespace TradePro.Api.Oms;

/// <summary>
/// THE unified position-reconciliation loop. Replaces per-broker, per-order
/// status polling (which had a different failure mode for every broker and
/// every edge) with ONE invariant applied to every broker identically:
///
///   OMS-believed positions must equal the broker's golden-source positions.
///   Drift is either auto-settled (safe cases) or SURFACED LOUDLY (everything
///   else) — never silently left, never fabricated.
///
/// Auto-settle rule (deliberately narrow, no-false-positives):
///   • An in-flight SELL whose symbol the broker no longer holds (netted flat)
///     has executed → record the fill (real price if the broker can supply it,
///     else 0-flagged). This is the clean long-close case (e.g. AMAT/TSLA sold
///     at T212 but never recorded → inflated "invested").
///   • Everything else — a sell the broker STILL holds (e.g. a short the clone
///     opened), a failed golden-source read, a stuck BUY — is NOT settled. It
///     is written to the per-broker drift list and shown on the cockpit.
///
/// Adding a broker = adding an <see cref="IBrokerPositionSource"/>. The loop
/// never changes. Grace window avoids racing a just-placed order.
/// </summary>
public sealed class GoldenSourceReconciler : BackgroundService
{
    private static readonly string[] InFlight =
    {
        OmsState.Submitted,
        OmsState.Working,
        OmsState.PartiallyFilled,
    };

    private readonly IServiceProvider _services;
    private readonly IReconciliationStatus _status;
    private readonly ILogger<GoldenSourceReconciler> _log;
    private readonly TimeSpan _tick;
    private readonly TimeSpan _grace;
    private readonly bool _autoFlattenDemoDrift;

    public GoldenSourceReconciler(
        IServiceProvider services,
        IReconciliationStatus status,
        IConfiguration config,
        ILogger<GoldenSourceReconciler> log)
    {
        _services = services;
        _status = status;
        _log = log;
        _tick = TimeSpan.FromSeconds(Math.Clamp(config.GetValue<int?>("Oms:ReconcileSeconds") ?? 60, 15, 600));
        _grace = TimeSpan.FromSeconds(Math.Clamp(config.GetValue<int?>("Oms:ReconcileGraceSeconds") ?? 300, 60, 3600));
        // Kill-switch for the demo auto-clear (default on). Set Oms:AutoFlattenDemoDrift=false
        // to make even the demo IBKR clone flag-only if the auto-clear ever misbehaves.
        _autoFlattenDemoDrift = config.GetValue<bool?>("Oms:AutoFlattenDemoDrift") ?? true;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation(
            "GoldenSourceReconciler started: tick={Tick}s grace={Grace}s",
            _tick.TotalSeconds, _grace.TotalSeconds);
        try { await Task.Delay(TimeSpan.FromSeconds(15), stoppingToken); }
        catch (OperationCanceledException) { return; }

        while (!stoppingToken.IsCancellationRequested)
        {
            try { await ReconcileAllAsync(stoppingToken); }
            catch (Exception ex) when (ex is not OperationCanceledException)
            { _log.LogError(ex, "GoldenSourceReconciler tick threw"); }
            try { await Task.Delay(_tick, stoppingToken); }
            catch (OperationCanceledException) { return; }
        }
    }

    /// <summary>One full pass over every broker. Public so an operator endpoint
    /// can trigger it on demand.</summary>
    public async Task<IReadOnlyList<ReconciliationBrokerStatus>> ReconcileAllAsync(CancellationToken ct)
    {
        using var scope = _services.CreateScope();
        var sources = scope.ServiceProvider.GetServices<IBrokerPositionSource>();
        var oms = scope.ServiceProvider.GetRequiredService<IOmsService>();
        var results = new List<ReconciliationBrokerStatus>();
        foreach (var src in sources)
        {
            if (ct.IsCancellationRequested) break;
            try { results.Add(await ReconcileBrokerAsync(src, oms, ct)); }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogError(ex, "reconcile {Broker} threw", src.BrokerLabel);
                var s = new ReconciliationBrokerStatus(
                    src.BrokerLabel, DateTime.UtcNow, false, ex.Message, 0, 0, 0,
                    new[] { $"reconcile threw: {ex.Message}" });
                _status.Record(s);
                results.Add(s);
            }
        }
        return results;
    }

    private async Task<ReconciliationBrokerStatus> ReconcileBrokerAsync(
        IBrokerPositionSource src, IOmsService oms, CancellationToken ct)
    {
        var all = await oms.ListAsync(InFlight, 500);
        var openSells = all.Where(o =>
            string.Equals(o.Broker, src.BrokerLabel, StringComparison.OrdinalIgnoreCase)
            && string.Equals(o.Side, "SELL", StringComparison.OrdinalIgnoreCase)
            && !string.IsNullOrEmpty(o.BrokerOrderId)
            && o.CreatedAtUtc < DateTime.UtcNow - _grace).ToList();

        // ALWAYS read positions — even with 0 open sells — so a broken/paused
        // golden-source feed is surfaced rather than silently assumed healthy.
        var read = await src.ReadPositionsAsync(ct);
        var drift = new List<string>();

        if (!read.Ok)
        {
            if (openSells.Count > 0)
                drift.Add($"golden-source read FAILED ({read.Error}) — {openSells.Count} open sell(s) UNVERIFIED");
            else
                drift.Add($"golden-source read FAILED ({read.Error})");
            _log.LogWarning(
                "reconcile {Broker}: golden-source read failed ({Err}) — NOT settling (a failed read is not 'flat')",
                src.BrokerLabel, read.Error);
            var failed = new ReconciliationBrokerStatus(
                src.BrokerLabel, DateTime.UtcNow, false, read.Error, openSells.Count, 0, 0, drift);
            _status.Record(failed);
            return failed;
        }

        var held = PositionReconcile.HeldByCanonical(read.Positions);
        int settled = 0, unconfirmed = 0;

        foreach (var o in openSells)
        {
            if (ct.IsCancellationRequested) break;
            if (!PositionReconcile.SellExecuted(held, o.Symbol))
            {
                // Broker STILL holds it (a long not yet exited, or a short this
                // sell opened) — ambiguous, needs execution-level confirmation.
                // Surface, do not fabricate.
                held.TryGetValue(PositionReconcile.Canonical(o.Symbol), out var q);
                drift.Add($"{o.Symbol} SELL qty={o.Qty} still SUBMITTED but broker holds {q} — not auto-settling (needs execution check)");
                continue;
            }

            decimal price = 0m;
            try { price = (await src.TryGetFillPriceAsync(o.BrokerOrderId!, ct)) ?? 0m; }
            catch (Exception ex) when (ex is not OperationCanceledException)
            { _log.LogDebug(ex, "reconcile {Broker}: price lookup threw for {Sym}", src.BrokerLabel, o.Symbol); }

            await oms.RecordFillAsync(
                o.Id, qty: o.Qty, price: price, fee: 0m, currency: "USD",
                brokerFillId: $"assumed_via_position_reconcile:{o.BrokerOrderId}",
                actor: $"reconciler:{src.BrokerLabel}");
            settled++;
            if (price == 0m)
            {
                unconfirmed++;
                drift.Add($"{o.Symbol} SELL settled FILLED (broker flat) but price UNCONFIRMED — attribution pending");
            }
            _log.LogInformation(
                "reconcile {Broker}: {Sym} SELL qty={Qty} FILLED via golden-source position reconcile "
                + "(broker holds none), price={Price}{Note}",
                src.BrokerLabel, o.Symbol, o.Qty, price, price == 0m ? " (unconfirmed)" : "");
        }

        // --- Standing-position reconcile (FILLED book vs the broker's held book) ---
        // The loop above only settles in-flight SELLs. A position recorded FILLED
        // that the broker never actually held — the IBKR clone's ack-less gateway
        // wrote unconfirmed fills, opening phantom shorts — is invisible to it and
        // ACCUMULATES silently (the 34 shorts that survived a broker reset). Compare
        // the OMS net book to the broker: FLAG every mismatch loudly; for a source
        // that opts into auto-clear (the demo IBKR clone, whose execution is
        // unconfirmed so the broker is the sole truth) net-zero any symbol the
        // broker holds NONE of. Only brokerQty==0 is auto-cleared — a partial
        // mismatch is ambiguous (whose strategy owns the residual) so it is flagged,
        // never guessed. Grace-guarded so we never race a just-recorded fill.
        int flattened = 0;
        var canAutoClear = _autoFlattenDemoDrift && src.AutoFlattenStandingDrift;
        var omsBook = (await oms.ListPositionsAsync(null))
            .Where(p => string.Equals(p.Broker, src.BrokerLabel, StringComparison.OrdinalIgnoreCase))
            .ToList();
        foreach (var pos in omsBook)
        {
            if (ct.IsCancellationRequested) break;
            held.TryGetValue(PositionReconcile.Canonical(pos.Symbol), out var brokerQty);
            var action = PositionReconcile.ClassifyStanding(
                pos.Quantity, brokerQty, pos.LastFillAtUtc, canAutoClear, _grace, DateTime.UtcNow);
            if (action is PositionReconcile.StandingAction.InSync or PositionReconcile.StandingAction.SkipGrace)
                continue;

            if (action == PositionReconcile.StandingAction.AutoClear)
            {
                // Offset to zero, attributed to the SAME strategy so the per-strategy
                // view zeroes cleanly. Price 0-flagged (unconfirmed) — never fabricate.
                var side = pos.Quantity < 0m ? "BUY" : "SELL";
                var qty = Math.Abs(pos.Quantity);
                var intent = new OrderIntent(
                    ClientOrderId: Guid.NewGuid(),
                    Broker: src.BrokerLabel,
                    Symbol: pos.Symbol,
                    Side: side,
                    Qty: qty,
                    OrderType: "MKT",
                    StrategyId: pos.StrategyId == "(unattributed)" ? null : pos.StrategyId,
                    PlacedBy: "STRATEGY_AUTO");
                var order = await oms.EnqueueAsync(intent, $"reconciler:{src.BrokerLabel}");
                await oms.RecordFillAsync(
                    order.Id, qty, price: 0m, fee: 0m, currency: "USD",
                    brokerFillId: "standing_reconcile_broker_flat",
                    actor: $"reconciler:{src.BrokerLabel}");
                flattened++;
                drift.Add($"{pos.Symbol} standing OMS={pos.Quantity}→0 auto-cleared (broker holds none; price unconfirmed)");
                _log.LogInformation(
                    "reconcile {Broker}: {Sym} standing position {Qty} auto-cleared to broker-flat",
                    src.BrokerLabel, pos.Symbol, pos.Quantity);
            }
            else
            {
                drift.Add($"{pos.Symbol} standing OMS={pos.Quantity} but broker={brokerQty} — not auto-reconciling (needs check)");
            }
        }

        var status = new ReconciliationBrokerStatus(
            src.BrokerLabel, DateTime.UtcNow, true, null,
            openSells.Count, settled + flattened, unconfirmed, drift);
        _status.Record(status);
        return status;
    }
}
