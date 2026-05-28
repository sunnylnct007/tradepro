using Microsoft.Extensions.Logging;

namespace TradePro.Api.Oms;

/// <summary>
/// Process-local OMS mode store. Persists nothing — restarting the
/// API box defaults back to Manual (safest). When we want survival
/// across deploys, swap to a Postgres-backed impl and persist the
/// mode change as an oms_order_events-style audit row.
///
/// Switching Auto → Manual calls IOmsService.CancelAllOpenAsync with
/// reason "MODE_FLIP" so the operator never holds in-flight orders
/// after stepping away from auto-mode.
/// </summary>
public sealed class InMemoryOmsModeService : IOmsModeService
{
    private readonly IOmsService _oms;
    private readonly ILogger<InMemoryOmsModeService> _log;
    private readonly object _gate = new();
    private OmsMode _current = OmsMode.Manual;

    public InMemoryOmsModeService(IOmsService oms, ILogger<InMemoryOmsModeService> log)
    {
        _oms = oms;
        _log = log;
    }

    public OmsMode Current
    {
        get { lock (_gate) return _current; }
    }

    public async Task<OmsMode> SetAsync(OmsMode target, string actor)
    {
        OmsMode prior;
        lock (_gate)
        {
            prior = _current;
            _current = target;
        }

        // The cancel-on-flip contract: leaving Auto for Manual nukes
        // any in-flight orders the strategy queued. Flipping the
        // other way (Manual → Auto) leaves manual-mode rows alone —
        // those were explicitly approved by a human and shouldn't be
        // wiped just because we re-engaged auto-mode.
        if (prior == OmsMode.Auto && target == OmsMode.Manual)
        {
            var cancelled = await _oms.CancelAllOpenAsync(actor, "MODE_FLIP_AUTO_TO_MANUAL");
            _log.LogInformation(
                "OMS mode flip Auto→Manual by {actor}: cancelled {count} open order(s) ({ids})",
                actor, cancelled.Count, string.Join(",", cancelled));
        }
        else
        {
            _log.LogInformation("OMS mode set to {target} by {actor} (prior: {prior})",
                target, actor, prior);
        }
        return target;
    }
}
