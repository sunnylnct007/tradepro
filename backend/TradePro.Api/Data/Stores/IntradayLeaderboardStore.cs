using Dapper;
using Npgsql;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Aggregates per-(symbol, strategy) outcomes across every completed
/// intraday session_request. Powers the leaderboard view that answers
/// the user's question: "if I'd used strategy X on symbol Y, would it
/// have made money?"
///
/// Data source: session_requests.result_summary JSONB. The intraday
/// engine writes per-symbol-per-strategy fill counts + realised P&L
/// into that blob on every Completed cycle, so a SQL aggregation over
/// the table gives a clean rollup with no extra ETL.
///
/// Tradeoff: re-scans every Completed row on each request — fine at
/// hundreds-of-rows scale, and avoids a parallel materialised
/// projection that we'd have to keep in sync. If the table grows past
/// O(10k) rows the query becomes worth caching; not yet.
/// </summary>
public interface IIntradayLeaderboardStore
{
    LeaderboardPayload Build();
}

public sealed record LeaderboardCell(
    string Symbol,
    int Sessions,
    int Fills,
    decimal RealizedPnlUsd,
    DateTime? LastSeenAtUtc);

public sealed record LeaderboardRow(
    string Strategy,
    LeaderboardCell[] BySymbol,
    int TotalSessions,
    int TotalFills,
    decimal TotalRealizedPnlUsd);

public sealed record LeaderboardPayload(
    DateTime GeneratedAtUtc,
    int SessionCount,
    DateTime? LastSessionAtUtc,
    string[] Symbols,
    LeaderboardRow[] Strategies);

public sealed class PostgresIntradayLeaderboardStore : IIntradayLeaderboardStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresIntradayLeaderboardStore(NpgsqlDataSource db) { _db = db; }

    public LeaderboardPayload Build()
    {
        using var conn = _db.OpenConnection();

        // jsonb_array_elements LATERAL unrolls the nested
        // result_summary.results[*].strategies[*] arrays into a flat
        // (session, symbol, strategy, fills, pnl) projection that we
        // can GROUP BY. Coalesce null/missing realized_pnl to 0 so a
        // strategy that emitted zero orders still shows a 0 cell
        // rather than disappearing from the matrix.
        const string sql = @"
WITH per_strategy AS (
    SELECT
        sr.completed_at_utc AS session_at,
        r->>'symbol' AS symbol,
        s->>'strategy' AS strategy,
        COALESCE((s->>'fills')::int, 0) AS fills,
        COALESCE((s->>'realized_pnl_usd')::numeric, 0) AS realized_pnl_usd
    FROM session_requests sr
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(sr.result_summary->'results', '[]'::jsonb)) AS r
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(r->'strategies', '[]'::jsonb)) AS s
    WHERE sr.kind = 'intraday'
      AND sr.state = 'Completed'
      AND r->>'symbol' IS NOT NULL
      AND s->>'strategy' IS NOT NULL
)
SELECT symbol, strategy,
       COUNT(*) AS sessions,
       SUM(fills) AS fills,
       SUM(realized_pnl_usd) AS realized_pnl_usd,
       MAX(session_at) AS last_seen_at_utc
FROM per_strategy
GROUP BY symbol, strategy
ORDER BY strategy, symbol;";

        var rows = conn.Query<RawRow>(sql).ToList();

        const string countSql = @"
SELECT COUNT(*) AS session_count,
       MAX(completed_at_utc) AS last_session_at_utc
FROM session_requests
WHERE kind = 'intraday' AND state = 'Completed';";
        var meta = conn.QueryFirstOrDefault<MetaRow>(countSql) ?? new MetaRow(0, null);

        var symbols = rows.Select(r => r.symbol).Distinct().OrderBy(s => s).ToArray();
        var strategies = rows.GroupBy(r => r.strategy)
            .OrderBy(g => g.Key)
            .Select(g =>
            {
                var bySymbol = symbols.Select(sym =>
                {
                    var hit = g.FirstOrDefault(r => r.symbol == sym);
                    if (hit is null)
                    {
                        return new LeaderboardCell(sym, 0, 0, 0m, null);
                    }
                    return new LeaderboardCell(
                        Symbol: sym,
                        Sessions: hit.sessions,
                        Fills: hit.fills,
                        RealizedPnlUsd: hit.realized_pnl_usd,
                        LastSeenAtUtc: hit.last_seen_at_utc);
                }).ToArray();
                return new LeaderboardRow(
                    Strategy: g.Key,
                    BySymbol: bySymbol,
                    TotalSessions: bySymbol.Sum(c => c.Sessions),
                    TotalFills: bySymbol.Sum(c => c.Fills),
                    TotalRealizedPnlUsd: bySymbol.Sum(c => c.RealizedPnlUsd));
            })
            .ToArray();

        return new LeaderboardPayload(
            GeneratedAtUtc: DateTime.UtcNow,
            SessionCount: meta.session_count,
            LastSessionAtUtc: meta.last_session_at_utc,
            Symbols: symbols,
            Strategies: strategies);
    }

    private sealed record RawRow(
        string symbol, string strategy, int sessions, int fills,
        decimal realized_pnl_usd, DateTime? last_seen_at_utc);

    private sealed record MetaRow(int session_count, DateTime? last_session_at_utc);
}
