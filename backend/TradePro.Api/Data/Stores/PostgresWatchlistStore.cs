using Dapper;
using Npgsql;
using TradePro.Api.Watchlists;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Postgres-backed watchlist store. The in-memory store had its
/// 13 universes hard-coded in C#; we keep that as the bootstrap
/// source and sync any missing rows into Postgres on construction.
///
/// Semantics:
/// <list type="bullet">
///   <item>First boot: every watchlist in <see cref="InMemoryWatchlistStore"/>
///   gets <c>INSERT … ON CONFLICT DO NOTHING</c>'d into the DB.</item>
///   <item>Subsequent boots: existing rows are left alone. Code-side
///   edits to watchlists won't propagate automatically — operator
///   either runs a re-seed script or modifies rows directly.</item>
///   <item>Runtime API (future): when we add edit endpoints, they
///   write to Postgres and reads pick up the changes immediately.</item>
/// </list>
/// </summary>
public sealed class PostgresWatchlistStore : IWatchlistStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresWatchlistStore(NpgsqlDataSource db, ILogger<PostgresWatchlistStore> log)
    {
        _db = db;
        SeedIfEmpty(log);
    }

    public WatchlistDto? Get(string name)
    {
        using var conn = _db.OpenConnection();
        var meta = conn.QueryFirstOrDefault<WatchlistRow>(@"
            SELECT name, currency, region FROM watchlists
            WHERE LOWER(name) = LOWER(@name);",
            new { name });
        if (meta is null) return null;

        var items = conn.Query<WatchlistItemRow>(@"
            SELECT symbol, label, kind, position FROM watchlist_items
            WHERE watchlist_name = @name
            ORDER BY position ASC;",
            new { name = meta.name }).ToList();

        return new WatchlistDto(
            Name: meta.name,
            Currency: meta.currency,
            Region: meta.region,
            Items: items.Select(i => new WatchlistItem(i.symbol, i.label, i.kind)).ToList());
    }

    public IEnumerable<string> Keys
    {
        get
        {
            using var conn = _db.OpenConnection();
            return conn.Query<string>("SELECT name FROM watchlists ORDER BY name;").ToList();
        }
    }

    private void SeedIfEmpty(ILogger log)
    {
        using var conn = _db.OpenConnection();
        var count = conn.ExecuteScalar<int>("SELECT COUNT(*) FROM watchlists;");
        if (count > 0)
        {
            log.LogInformation("watchlists table has {count} rows — skipping seed", count);
            return;
        }
        log.LogInformation("watchlists table empty — seeding from InMemoryWatchlistStore defaults");

        var bootstrap = new InMemoryWatchlistStore();
        using var tx = conn.BeginTransaction();
        foreach (var key in bootstrap.Keys)
        {
            var w = bootstrap.Get(key);
            if (w is null) continue;
            conn.Execute(@"
                INSERT INTO watchlists (name, currency, region)
                VALUES (@name, @currency, @region)
                ON CONFLICT (name) DO NOTHING;",
                new { name = key, w.Currency, w.Region },
                transaction: tx);
            var pos = 0;
            foreach (var item in w.Items)
            {
                conn.Execute(@"
                    INSERT INTO watchlist_items (watchlist_name, symbol, label, kind, position)
                    VALUES (@watchlist, @symbol, @label, @kind, @position)
                    ON CONFLICT (watchlist_name, symbol) DO NOTHING;",
                    new { watchlist = key, item.Symbol, item.Label, item.Kind, position = pos },
                    transaction: tx);
                pos++;
            }
        }
        tx.Commit();
        log.LogInformation("watchlist seed complete");
    }

    private sealed record WatchlistRow(string name, string currency, string region);
    private sealed record WatchlistItemRow(string symbol, string label, string kind, int position);
}
