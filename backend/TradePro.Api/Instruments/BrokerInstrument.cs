namespace TradePro.Api.Instruments;

/// <summary>
/// Broker-NEUTRAL view of one tradeable instrument as it exists in a
/// broker's own registry. This is the only shape the cross-broker
/// instrument-identity machinery (BrokerTickerMapBuilder, the matcher)
/// understands — it never sees a T212/IG/IBKR-specific record. Each
/// broker integration adapts its native registry row into this via an
/// <see cref="IBrokerInstrumentCatalog"/>.
///
/// Why broker-agnostic: the strategy/universe layer speaks Yahoo-style
/// tickers ("META", "AAPL"). Every broker carries its OWN code for the
/// same security (Meta is "FB_US_EQ" on T212, "US.D.FB.CASH.IP"-style on
/// IG, a numeric conid on IBKR). The map from one to the other is DATA
/// derived from the broker's catalog — never a hardcoded suffix rule.
/// ISIN is the durable cross-broker bridge: the same security has the
/// same ISIN everywhere, so once ISINs are backfilled on the universe
/// side, matching becomes broker-independent.
///
/// <see cref="ShortName"/> is the broker's OWN human ticker for the
/// security ("JEF" on T212 even though its order code is "LUK_US_EQ").
/// It's the bridge for RE-TICKERED / re-listed names: the universe
/// speaks the human ticker, so when the order-code root differs we can
/// still match on the broker's short ticker. Null when a broker doesn't
/// expose a separate short code.
/// </summary>
public sealed record BrokerInstrument(
    string BrokerTicker,
    string? Name,
    string? Isin,
    string? AssetClass,
    string? Currency,
    string? ShortName = null);
