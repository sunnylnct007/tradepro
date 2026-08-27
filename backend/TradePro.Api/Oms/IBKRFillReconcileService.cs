namespace TradePro.Api.Oms;

/// <summary>
/// Ticks the IBKR fill reconcile on a schedule.
///
/// WHY (27 Aug 2026): the fill-recording path had NO scheduled caller. It ran
/// only when someone POSTed /integrations/ibkr/reconcile-oms by hand.
/// GoldenSourceReconciler ticks every 60s, but it reconciles POSITIONS via
/// IBrokerPositionSource and never reads orders or executions.
///
/// So the fixes that closed the two-month fill blindness -- the primed blotter
/// read and the execution order_id -- would have worked every time they were
/// tested by hand and never once on their own. A Swing order would fill at the
/// broker and sit in the OMS as SUBMITTED with a null price, which looks
/// EXACTLY like the bug that was just closed. A fix that only works when
/// watched is not a fix.
///
/// Deliberately a separate service from GoldenSourceReconciler rather than a
/// call inside its loop: a broker read that starts failing must not take the
/// position reconcile down with it, and the two answer different questions.
/// </summary>
public sealed class IBKRFillReconcileService : BackgroundService
{
    private readonly IServiceProvider _services;
    private readonly ILogger<IBKRFillReconcileService> _log;
    private readonly TimeSpan _tick;
    private readonly bool _enabled;

    public IBKRFillReconcileService(
        IServiceProvider services, IConfiguration config,
        ILogger<IBKRFillReconcileService> log)
    {
        _services = services;
        _log = log;
        _tick = TimeSpan.FromSeconds(
            Math.Clamp(config.GetValue<int?>("Oms:IbkrFillReconcileSeconds") ?? 90, 30, 900));
        _enabled = config.GetValue<bool?>("Oms:IbkrFillReconcileEnabled") ?? true;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_enabled)
        {
            _log.LogWarning(
                "IBKRFillReconcileService DISABLED by config — IBKR fills will not be "
                + "recorded unless /integrations/ibkr/reconcile-oms is called manually");
            return;
        }
        _log.LogInformation("IBKRFillReconcileService started: tick={Tick}s", _tick.TotalSeconds);
        try { await Task.Delay(TimeSpan.FromSeconds(25), stoppingToken); }
        catch (OperationCanceledException) { return; }

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                using var scope = _services.CreateScope();
                var reconciler = scope.ServiceProvider.GetRequiredService<IBKRFillReconciler>();
                var r = await reconciler.RunAsync(stoppingToken);
                // Only speak when something happened or something is wrong. A
                // quiet market must not fill the log with noise, but a broker
                // read that has started failing must never be silent.
                if (!r.Ok && r.Error != "IBKR disabled")
                    _log.LogError("IBKR fill reconcile failed: {Error}", r.Error);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.LogError(ex, "IBKR fill reconcile tick threw");
            }
            try { await Task.Delay(_tick, stoppingToken); }
            catch (OperationCanceledException) { return; }
        }
    }
}
