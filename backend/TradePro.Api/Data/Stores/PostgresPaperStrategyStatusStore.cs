using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Postgres-backed overrides for the strategy promotion lifecycle.
/// See migration 008_paper_strategy_status.sql for the schema.
/// </summary>
public sealed class PostgresPaperStrategyStatusStore : IPaperStrategyStatusStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresPaperStrategyStatusStore(NpgsqlDataSource db) { _db = db; }

    public StrategyStatusOverride? Get(string strategyId)
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<Row>(@"
            SELECT strategy_id AS StrategyId,
                   status,
                   updated_at_utc AS UpdatedAtUtc,
                   updated_by AS UpdatedBy
            FROM paper_strategy_status
            WHERE strategy_id = @strategyId;",
            new { strategyId });
        return row?.ToRecord();
    }

    public IReadOnlyList<StrategyStatusOverride> ListAll()
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<Row>(@"
            SELECT strategy_id AS StrategyId,
                   status,
                   updated_at_utc AS UpdatedAtUtc,
                   updated_by AS UpdatedBy
            FROM paper_strategy_status
            ORDER BY strategy_id;");
        return rows.Select(r => r.ToRecord()).ToList();
    }

    public StrategyStatusOverride Upsert(string strategyId, string status, string updatedBy)
    {
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO paper_strategy_status (strategy_id, status, updated_at_utc, updated_by)
            VALUES (@strategyId, @status, NOW(), @updatedBy)
            ON CONFLICT (strategy_id) DO UPDATE
              SET status = EXCLUDED.status,
                  updated_at_utc = EXCLUDED.updated_at_utc,
                  updated_by = EXCLUDED.updated_by;",
            new { strategyId, status, updatedBy });
        return Get(strategyId)!;
    }

    public bool Clear(string strategyId)
    {
        using var conn = _db.OpenConnection();
        var n = conn.Execute(@"
            DELETE FROM paper_strategy_status WHERE strategy_id = @strategyId;",
            new { strategyId });
        return n > 0;
    }

    // Internal row shape — Dapper materialises into this then we
    // convert to the public record (lets us keep the public type
    // immutable + record-shaped).
    private sealed class Row
    {
        public string StrategyId { get; set; } = "";
        public string Status { get; set; } = "";
        public DateTime UpdatedAtUtc { get; set; }
        public string UpdatedBy { get; set; } = "";

        public StrategyStatusOverride ToRecord() =>
            new(StrategyId, Status, UpdatedAtUtc, UpdatedBy);
    }
}
