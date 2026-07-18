using System.Collections.Concurrent;

namespace TradePro.Api.Oms;

/// <summary>Last reconcile result for one broker — the fail-loud surface. The
/// cockpit reads this so position drift is VISIBLE, never silently "fine".</summary>
public sealed record ReconciliationBrokerStatus(
    string Broker,
    DateTime LastRunUtc,
    bool ReadOk,
    string? ReadError,
    int OpenSellsChecked,
    int SettledExecuted,
    int UnconfirmedPrice,
    IReadOnlyList<string> Drift)
{
    /// <summary>RED when the golden-source read failed OR there is unresolved
    /// drift the reconciler could not safely settle. GREEN only when the read
    /// was clean and nothing needs a human.</summary>
    public bool NeedsAttention => !ReadOk || Drift.Count > 0;
}

/// <summary>In-memory registry of the latest per-broker reconcile status.
/// Singleton; written by <see cref="GoldenSourceReconciler"/>, read by the
/// /api/oms/reconciliation-status endpoint.</summary>
public interface IReconciliationStatus
{
    void Record(ReconciliationBrokerStatus status);
    IReadOnlyList<ReconciliationBrokerStatus> Snapshot();
}

public sealed class ReconciliationStatus : IReconciliationStatus
{
    private readonly ConcurrentDictionary<string, ReconciliationBrokerStatus> _byBroker =
        new(StringComparer.OrdinalIgnoreCase);

    public void Record(ReconciliationBrokerStatus status) =>
        _byBroker[status.Broker] = status;

    public IReadOnlyList<ReconciliationBrokerStatus> Snapshot() =>
        _byBroker.Values.OrderBy(s => s.Broker, StringComparer.Ordinal).ToList();
}
