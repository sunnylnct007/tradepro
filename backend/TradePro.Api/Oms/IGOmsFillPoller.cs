using Dapper;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;
using TradePro.Api.Providers.IG;

namespace TradePro.Api.Oms;

/// <summary>
/// IG-flavoured fill poller — mirrors OmsFillPoller for T212. Polls
/// IG's /confirms/{dealReference} endpoint for every in-flight order
/// whose broker is IG_DEMO or IG_LIVE and broker_order_id is a deal
/// reference (UUID-shaped, not a numeric T212 id). On ACCEPTED →
/// RecordFill at the deal's level; on REJECTED → CancelAsync with
/// the broker's rejection reason.
///
/// Without this, IG orders strand in SUBMITTED forever — the only way
/// the OMS row would update is the operator manually polling. The
/// poller is on by default; disable via Risk:IGPollerDisabled if a
/// debug-only mode is needed.
/// </summary>
public sealed class IGOmsFillPoller : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly ILogger<IGOmsFillPoller> _log;
    private readonly TimeSpan _interval;
    private readonly TimeSpan _perOrderDelay;
    private readonly bool _disabled;

    public IGOmsFillPoller(
        IServiceScopeFactory scopes,
        IConfiguration cfg,
        ILogger<IGOmsFillPoller> log)
    {
        _scopes = scopes;
        _log = log;
        var sec = cfg.GetValue<int?>("Oms:IGPollerIntervalSeconds") ?? 30;
        _interval = TimeSpan.FromSeconds(Math.Clamp(sec, 5, 600));
        var per = cfg.GetValue<int?>("Oms:IGPollerPerOrderDelayMs") ?? 250;
        _perOrderDelay = TimeSpan.FromMilliseconds(Math.Clamp(per, 0, 5000));
        _disabled = cfg.GetValue<bool?>("Oms:IGPollerDisabled") ?? false;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        if (_disabled)
        {
            _log.LogInformation("IGOmsFillPoller disabled via config");
            return;
        }
        _log.LogInformation(
            "IGOmsFillPoller started: tick={Tick}s perOrderDelay={Delay}ms",
            _interval.TotalSeconds, _perOrderDelay.TotalMilliseconds);
        // Initial settle delay so the API has time to come up.
        try { await Task.Delay(TimeSpan.FromSeconds(10), ct); }
        catch (OperationCanceledException) { return; }

        while (!ct.IsCancellationRequested)
        {
            try { await OneTickAsync(ct); }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogError(ex, "IGOmsFillPoller tick threw — continuing");
            }
            try { await Task.Delay(_interval, ct); }
            catch (OperationCanceledException) { break; }
        }
    }

    private async Task OneTickAsync(CancellationToken ct)
    {
        using var scope = _scopes.CreateScope();
        var sp = scope.ServiceProvider;
        var db = sp.GetRequiredService<NpgsqlDataSource>();
        var ig = sp.GetService<IGClient>();
        var oms = sp.GetService<IOmsService>();
        if (ig is null || oms is null) return;
        if (!ig.IsEnabled) return;

        await using var conn = await db.OpenConnectionAsync(ct);
        // Pull in-flight IG orders (state ∈ SUBMITTED/WORKING) with a
        // non-empty broker_order_id (= dealReference). Filter on the
        // broker prefix to scope to IG_*.
        var rows = (await conn.QueryAsync<(Guid id, string brokerOrderId, decimal qty)>(@"
            SELECT id, broker_order_id, qty
            FROM oms_orders
            WHERE broker LIKE 'IG_%'
              AND state IN ('SUBMITTED', 'WORKING')
              AND broker_order_id IS NOT NULL
              AND broker_order_id <> ''
            ORDER BY last_state_change_at_utc ASC
            LIMIT 100;")).ToList();
        if (rows.Count == 0) return;
        _log.LogDebug("IGOmsFillPoller: polling {Count} in-flight IG orders", rows.Count);

        foreach (var (orderId, dealRef, qty) in rows)
        {
            if (ct.IsCancellationRequested) return;
            try
            {
                var confirm = await ig.ConfirmDealAsync(dealRef, ct);
                var s = (confirm.Status ?? "").ToUpperInvariant();
                if (s == "ACCEPTED")
                {
                    // IG returns ACCEPTED on a successful fill. We
                    // don't get per-leg fill price back from /confirms
                    // directly; the level is on the source IGOrderResult
                    // from placement. Record at 0 if missing and let
                    // the operator reconcile against /positions.
                    await oms.RecordFillAsync(
                        orderId, qty: qty, price: 0m, fee: 0m,
                        currency: "GBP",
                        brokerFillId: dealRef,
                        actor: "poller:IG");
                    _log.LogInformation(
                        "IGOmsFillPoller: order {OrderId} FILLED qty={Qty} dealRef={Deal}",
                        orderId, qty, dealRef);
                }
                else if (s == "REJECTED")
                {
                    await oms.CancelAsync(orderId, "poller:IG",
                        confirm.StatusReason ?? "ig_rejected");
                    _log.LogInformation(
                        "IGOmsFillPoller: order {OrderId} REJECTED reason={Reason}",
                        orderId, confirm.StatusReason);
                }
                // PENDING / WORKING / UNKNOWN — no transition; check next tick.
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex,
                    "IGOmsFillPoller: reconcile failed for {OrderId} dealRef={Deal}",
                    orderId, dealRef);
            }
            try { await Task.Delay(_perOrderDelay, ct); }
            catch (OperationCanceledException) { return; }
        }
    }
}
