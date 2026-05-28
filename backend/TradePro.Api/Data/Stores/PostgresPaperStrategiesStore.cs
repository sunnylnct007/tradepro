using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Single-row paper-strategies catalog, persisted in the
/// <c>paper_strategies</c> table. The CHECK constraint on the id
/// column enforces the single-row invariant at the database level —
/// no race possible.
/// </summary>
public sealed class PostgresPaperStrategiesStore : IPaperStrategiesStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresPaperStrategiesStore(NpgsqlDataSource db) { _db = db; }

    public void Put(JsonElement payload)
    {
        var jsonText = JsonbHelpers.ToJsonb(payload);
        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO paper_strategies (id, payload, updated_at)
            VALUES ('singleton', @payload::jsonb, NOW())
            ON CONFLICT (id) DO UPDATE
                SET payload = EXCLUDED.payload,
                    updated_at = NOW();",
            new { payload = jsonText });
    }

    public JsonElement? Get()
    {
        using var conn = _db.OpenConnection();
        var text = conn.QueryFirstOrDefault<string>(
            "SELECT payload::text FROM paper_strategies WHERE id = 'singleton'");
        return text is null ? null : JsonbHelpers.FromJsonb(text);
    }
}
