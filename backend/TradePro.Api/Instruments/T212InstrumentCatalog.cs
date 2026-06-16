using Microsoft.Extensions.Options;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Instruments;

/// <summary>
/// Adapts the T212 instruments registry into the broker-neutral
/// <see cref="IBrokerInstrumentCatalog"/> seam. This is the ONLY
/// T212-aware code in the instrument-identity stack — everything else
/// (the matcher, the builder, the registry) is broker-agnostic. IG/IBKR
/// drop in by writing a sibling of this file.
///
/// The native T212 ticker carries its own structure ("AAPL_US_EQ" =
/// ROOT_EXCHANGE_TYPE). We surface it VERBATIM as BrokerTicker (that is
/// the code orders must use) and only derive a coarse AssetClass from the
/// trailing type segment for callers that want to prefer equities.
/// </summary>
public sealed class T212InstrumentCatalog : IBrokerInstrumentCatalog
{
    private readonly Trading212InstrumentsService _instruments;
    private readonly IOptionsMonitor<Trading212Options> _options;

    public T212InstrumentCatalog(
        Trading212InstrumentsService instruments,
        IOptionsMonitor<Trading212Options> options)
    {
        _instruments = instruments;
        _options = options;
    }

    /// <summary>
    /// "T212_LIVE" when configured for live, otherwise "T212_DEMO".
    /// Mode "disabled" still reports DEMO so the registry can resolve a
    /// catalog for diagnostic rebuilds; GetAllAsync returns empty in that
    /// case, making the rebuild a no-op.
    /// </summary>
    public string Broker =>
        string.Equals(_options.CurrentValue.Mode, "live", StringComparison.OrdinalIgnoreCase)
            ? "T212_LIVE"
            : "T212_DEMO";

    public async Task<IReadOnlyList<BrokerInstrument>> GetAllAsync(CancellationToken ct)
    {
        var native = await _instruments.GetAllAsync(ct);
        if (native.Count == 0) return Array.Empty<BrokerInstrument>();

        var mapped = new List<BrokerInstrument>(native.Count);
        foreach (var i in native)
        {
            if (string.IsNullOrWhiteSpace(i.Ticker)) continue;
            mapped.Add(new BrokerInstrument(
                BrokerTicker: i.Ticker,
                Name: i.Name ?? i.ShortName,
                Isin: i.Isin,
                AssetClass: DeriveAssetClass(i.Ticker, i.Type),
                Currency: i.CurrencyCode,
                // T212's shortName is the human ticker ("JEF") even when the
                // order code re-lists under a legacy root ("LUK_US_EQ") — the
                // bridge that lets the matcher resolve re-tickered names.
                ShortName: i.ShortName));
        }
        return mapped;
    }

    /// <summary>
    /// Coarse asset class for tie-breaking in the matcher. T212 encodes
    /// the type in the ticker's trailing segment ("_EQ", "_ETF"); fall
    /// back to the explicit Type field when present.
    /// </summary>
    private static string? DeriveAssetClass(string ticker, string? type)
    {
        if (ticker.EndsWith("_EQ", StringComparison.OrdinalIgnoreCase)) return "EQUITY";
        if (ticker.Contains("_ETF", StringComparison.OrdinalIgnoreCase)) return "ETF";
        if (!string.IsNullOrWhiteSpace(type)) return type.ToUpperInvariant();
        return null;
    }
}
