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

        // universe_symbols.isin (migration 048) is the canonical
        // cross-broker key. It starts null — the universe is built from
        // Wikipedia/curated lists that carry only ticker+name — and is
        // BACKFILLED below from each confident match, so subsequent
        // rebuilds (and other brokers) pivot on ISIN instead of re-deriving
        // from fragile ticker/name heuristics.
        var rows = (await conn.QueryAsync<UniverseRow>(@"
            SELECT DISTINCT ticker, name, isin
            FROM universe_symbols
            WHERE ticker IS NOT NULL AND ticker <> ''")).AsList();

        var universe = rows
            .Select(r => (ticker: r.Ticker, name: r.Name, isin: r.Isin))
            .ToList();

        var matches = BrokerInstrumentMatcher.Resolve(instruments, universe);

        int exact = 0, byName = 0, byIsin = 0, unresolved = 0, isinBackfilled = 0;
        var unresolvedSamples = new List<string>();
        var byNameSamples = new List<string>();

        foreach (var m in matches)
        {
            switch (m.Method)
            {
                case MatchMethod.Exact:
                    exact++;
                    await UpsertAsync(conn, broker, m, ct);
                    isinBackfilled += await BackfillUniverseIsinAsync(conn, m, ct);
                    break;
                case MatchMethod.ByName:
                    // SAFETY RAIL: name matches are low-confidence (distinct
                    // issuers can share a name, e.g. MZTI→LANC). They must NEVER
                    // enter the live routing map — a wrong mapping mis-routes a
                    // real order. Park them in the review queue for a human to
                    // promote via /api/admin/instruments/promote-suggestion.
                    // No ISIN backfill either: an unconfirmed name match is not
                    // trustworthy enough to stamp a canonical identity.
                    byName++;
                    if (byNameSamples.Count < 25)
                        byNameSamples.Add($"{m.SourceTicker}→{m.BrokerTicker} (suggested, pending review)");
                    await UpsertSuggestionAsync(conn, broker, m, ct);
                    break;
                case MatchMethod.ByIsin:
                    byIsin++;
                    await UpsertAsync(conn, broker, m, ct);
                    isinBackfilled += await BackfillUniverseIsinAsync(conn, m, ct);
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
            + "byIsin={ByIsin} unresolved={Unresolved} isinBackfilled={IsinBackfilled} "
            + "(of {Total} universe symbols, {Catalog} catalog instruments)",
            broker, exact, byName, byIsin, unresolved, isinBackfilled,
            universe.Count, instruments.Count);

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

    /// <summary>Park a low-confidence (name) match in the review queue —
    /// NOT the live routing map. A human promotes it after confirming.</summary>
    private static async Task UpsertSuggestionAsync(
        NpgsqlConnection conn, string broker, InstrumentMatchResult m, CancellationToken ct)
    {
        await conn.ExecuteAsync(new CommandDefinition(@"
            INSERT INTO broker_ticker_map_suggestions
                (broker, source_ticker, suggested_broker_ticker, method, isin, created_at_utc)
            VALUES (@broker, @source, @brokerTicker, @method, @isin, NOW())
            ON CONFLICT (broker, source_ticker) DO UPDATE
                SET suggested_broker_ticker = EXCLUDED.suggested_broker_ticker,
                    method                  = EXCLUDED.method,
                    isin                    = EXCLUDED.isin,
                    created_at_utc          = NOW();",
            new
            {
                broker,
                source = m.SourceTicker,
                brokerTicker = m.BrokerTicker,
                method = m.Method.ToString().ToLowerInvariant(),
                isin = m.Isin,
            },
            cancellationToken: ct));
    }

    /// <summary>
    /// Stamp the canonical ISIN onto universe_symbols from a TRUSTED match
    /// (exact / ISIN) when the universe row has none yet. This bootstraps
    /// the cross-broker key from a broker's own registry: once stamped,
    /// later rebuilds and other brokers pivot on the ISIN
    /// (<see cref="BrokerInstrumentMatcher"/>'s preferred bridge) rather
    /// than re-deriving from ticker/name. Idempotent — only fills NULL/empty
    /// ISINs, so it never overwrites a confirmed value. Returns the number
    /// of universe rows updated (a ticker can span multiple universes).
    /// </summary>
    private static async Task<int> BackfillUniverseIsinAsync(
        NpgsqlConnection conn, InstrumentMatchResult m, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(m.Isin)) return 0;
        return await conn.ExecuteAsync(new CommandDefinition(@"
            UPDATE universe_symbols
               SET isin = @isin
             WHERE UPPER(ticker) = @ticker
               AND (isin IS NULL OR isin = '')",
            new { isin = m.Isin.Trim(), ticker = m.SourceTicker },
            cancellationToken: ct));
    }

    /// <summary>Whether a match method is trustworthy enough for the LIVE
    /// routing map. Exact + ISIN are unambiguous; name is not.</summary>
    public static bool IsTrustedForRouting(MatchMethod method) =>
        method is MatchMethod.Exact or MatchMethod.ByIsin;

    private sealed record UniverseRow(string Ticker, string? Name, string? Isin);
}
