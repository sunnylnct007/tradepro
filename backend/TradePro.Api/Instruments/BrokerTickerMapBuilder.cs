using Dapper;
using Npgsql;

namespace TradePro.Api.Instruments;

/// <summary>
/// Outcome of a rebuild: per-method counts plus a few human-readable
/// samples so an operator can eyeball that name-matching did the right
/// thing (e.g. "META→FB_US_EQ") and see which symbols still need manual
/// attention.
/// </summary>
public sealed record RebuildResult(
    string Broker,
    int Exact,
    int ByName,
    int ByIsin,
    int Unresolved,
    string[] UnresolvedSamples,
    string[] ByNameSamples);

/// <summary>
/// Rebuilds broker_ticker_map for a broker by reconciling the universe
/// constituents against that broker's instrument catalog. Fully
/// broker-agnostic: it asks the registry for a catalog and runs the pure
/// <see cref="BrokerInstrumentMatcher"/>. A broker with no catalog yet
/// (IG/IBKR today) yields an empty, harmless result.
///
/// This is DATA, not code: no ticker is hardcoded — every mapping comes
/// from the broker's own registry, and the note column records HOW each
/// row was derived so a later audit can distinguish exact/name/isin
/// matches from operator-entered overrides.
/// </summary>
public sealed class BrokerTickerMapBuilder
{
    private readonly IBrokerCatalogRegistry _registry;
    private readonly NpgsqlDataSource _db;
    private readonly ILogger<BrokerTickerMapBuilder> _log;

    public BrokerTickerMapBuilder(
        IBrokerCatalogRegistry registry,
        NpgsqlDataSource db,
        ILogger<BrokerTickerMapBuilder> log)
    {
        _registry = registry;
        _db = db;
        _log = log;
    }

    public async Task<RebuildResult> RebuildAsync(string broker, CancellationToken ct)
    {
        broker = (broker ?? string.Empty).Trim();

        var catalog = _registry.Resolve(broker);
        if (catalog is null)
        {
            _log.LogInformation(
                "broker_ticker_map rebuild skipped: no catalog registered for broker {Broker}",
                broker);
            return new RebuildResult(broker, 0, 0, 0, 0,
                Array.Empty<string>(), Array.Empty<string>());
        }

        var instruments = await catalog.GetAllAsync(ct);
        if (instruments.Count == 0)
        {
            _log.LogWarning(
                "broker_ticker_map rebuild for {Broker}: catalog returned 0 instruments "
                + "(integration disabled or cache empty); nothing to do",
                broker);
            return new RebuildResult(broker, 0, 0, 0, 0,
                Array.Empty<string>(), Array.Empty<string>());
        }

        await using var conn = await _db.OpenConnectionAsync(ct);

        // universe_symbols has no ISIN today; the matcher accepts an
        // optional ISIN so backfilled ISINs flow through with no code
        // change. SELECT it defensively in case the column lands later.
        var rows = (await conn.QueryAsync<UniverseRow>(@"
            SELECT DISTINCT ticker, name
            FROM universe_symbols
            WHERE ticker IS NOT NULL AND ticker <> ''")).AsList();

        var universe = rows
            .Select(r => (ticker: r.Ticker, name: r.Name, isin: (string?)null))
            .ToList();

        var matches = BrokerInstrumentMatcher.Resolve(instruments, universe);

        int exact = 0, byName = 0, byIsin = 0, unresolved = 0;
        var unresolvedSamples = new List<string>();
        var byNameSamples = new List<string>();

        foreach (var m in matches)
        {
            switch (m.Method)
            {
                case MatchMethod.Exact:
                    exact++;
                    await UpsertAsync(conn, broker, m, ct);
                    break;
                case MatchMethod.ByName:
                    byName++;
                    if (byNameSamples.Count < 25)
                        byNameSamples.Add($"{m.SourceTicker}→{m.BrokerTicker}");
                    await UpsertAsync(conn, broker, m, ct);
                    break;
                case MatchMethod.ByIsin:
                    byIsin++;
                    await UpsertAsync(conn, broker, m, ct);
                    break;
                default:
                    unresolved++;
                    if (unresolvedSamples.Count < 25)
                        unresolvedSamples.Add(m.SourceTicker);
                    break;
            }
        }

        _log.LogInformation(
            "broker_ticker_map rebuild for {Broker}: exact={Exact} byName={ByName} "
            + "byIsin={ByIsin} unresolved={Unresolved} (of {Total} universe symbols, "
            + "{Catalog} catalog instruments)",
            broker, exact, byName, byIsin, unresolved, universe.Count, instruments.Count);

        return new RebuildResult(
            broker, exact, byName, byIsin, unresolved,
            unresolvedSamples.ToArray(), byNameSamples.ToArray());
    }

    private static async Task UpsertAsync(
        NpgsqlConnection conn, string broker, InstrumentMatchResult m, CancellationToken ct)
    {
        await conn.ExecuteAsync(new CommandDefinition(@"
            INSERT INTO broker_ticker_map
                (broker, source_ticker, broker_ticker, isin, note, updated_at_utc)
            VALUES (@broker, @source, @brokerTicker, @isin, @note, NOW())
            ON CONFLICT (broker, source_ticker) DO UPDATE
                SET broker_ticker  = EXCLUDED.broker_ticker,
                    isin           = EXCLUDED.isin,
                    note           = EXCLUDED.note,
                    updated_at_utc = NOW();",
            new
            {
                broker,
                source = m.SourceTicker,
                brokerTicker = m.BrokerTicker,
                isin = m.Isin,
                note = m.Method.ToString().ToLowerInvariant(),
            },
            cancellationToken: ct));
    }

    private sealed record UniverseRow(string Ticker, string? Name);
}
