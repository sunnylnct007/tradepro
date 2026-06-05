namespace TradePro.Api.Instruments;

/// <summary>
/// Resolves a broker LABEL ("T212_DEMO", "T212_LIVE", "IG_DEMO", …) to
/// the <see cref="IBrokerInstrumentCatalog"/> that can enumerate that
/// broker's instruments — or null when no catalog is registered for the
/// broker yet (a fine, expected state: IG/IBKR have no catalog until
/// someone implements one). Matching is by Broker PREFIX so demo/live
/// variants of the same broker share a single catalog.
/// </summary>
public interface IBrokerCatalogRegistry
{
    IBrokerInstrumentCatalog? Resolve(string brokerLabel);
}

/// <summary>
/// Prefix-matching registry over the set of catalogs registered in DI.
/// A catalog whose <see cref="IBrokerInstrumentCatalog.Broker"/> is
/// "T212_DEMO" answers for any label that shares its broker family
/// ("T212_LIVE" → same T212 catalog), because the instrument REGISTRY is
/// identical across a broker's demo/live venues — only order routing
/// differs. The family key is everything before the first underscore
/// (so "T212_DEMO" → "T212").
/// </summary>
public sealed class BrokerCatalogRegistry : IBrokerCatalogRegistry
{
    private readonly IReadOnlyList<IBrokerInstrumentCatalog> _catalogs;

    public BrokerCatalogRegistry(IEnumerable<IBrokerInstrumentCatalog> catalogs)
        => _catalogs = catalogs.ToList();

    public IBrokerInstrumentCatalog? Resolve(string brokerLabel)
    {
        if (string.IsNullOrWhiteSpace(brokerLabel)) return null;
        var family = Family(brokerLabel);
        return _catalogs.FirstOrDefault(
            c => string.Equals(Family(c.Broker), family, StringComparison.OrdinalIgnoreCase));
    }

    private static string Family(string label)
    {
        var u = label.IndexOf('_');
        return (u > 0 ? label[..u] : label).Trim();
    }
}
