namespace TradePro.Api.Instruments;

/// <summary>
/// A broker's tradeable-instrument registry, surfaced as broker-NEUTRAL
/// <see cref="BrokerInstrument"/> rows. This is the ONLY seam the
/// cross-broker identity builder talks to — it never references a
/// concrete broker SDK. Add IG/IBKR support by implementing this once
/// against their instruments endpoint; nothing downstream changes.
/// </summary>
public interface IBrokerInstrumentCatalog
{
    /// <summary>
    /// The broker label this catalog serves, e.g. "T212_DEMO". The
    /// registry matches on its prefix ("T212") so demo + live share one
    /// catalog implementation.
    /// </summary>
    string Broker { get; }

    /// <summary>
    /// All instruments the broker lists, mapped to the neutral shape.
    /// Implementations should serve from cache and never throw — return
    /// an empty list when the integration is disabled/unavailable so the
    /// builder degrades to a no-op rather than failing.
    /// </summary>
    Task<IReadOnlyList<BrokerInstrument>> GetAllAsync(CancellationToken ct);
}
