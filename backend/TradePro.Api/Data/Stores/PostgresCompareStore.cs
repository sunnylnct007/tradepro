using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Compare-cache store keyed by universe. Largest payloads in the
/// system (50-150 KB per universe for the etf_all run) so we keep
/// a denormalised <c>summary</c> JSONB column for ListUniverses()
/// — the universe-pill row on the Decide page doesn't pay the
/// cost of pulling the full payload.
/// </summary>
public sealed class PostgresCompareStore : ICompareStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresCompareStore(NpgsqlDataSource db) { _db = db; }

    public CompareEnvelope Put(JsonElement payload)
    {
        var universe = JsonbHelpers.ReadString(payload, "universe") ?? "custom";
        var runId = JsonbHelpers.ReadString(payload, "run_id");
        var rankMetric = JsonbHelpers.ReadString(payload, "rank_metric");
        var generatedAt = JsonbHelpers.ReadDateOrNull(payload, "generated_at") ?? DateTime.UtcNow;
        var rowCount = JsonbHelpers.ReadArrayLength(payload, "rows");
        var receivedAt = DateTime.UtcNow;

        var summary = JsonSerializer.Serialize(new
        {
            universe,
            run_id = runId,
            rank_metric = rankMetric,
            generated_at = generatedAt,
            received_at = receivedAt,
            row_count = rowCount,
        });
        var payloadText = JsonbHelpers.ToJsonb(payload);

        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO compare_cache (universe, payload, summary, row_count, received_at_utc, updated_at)
            VALUES (@universe, @payloadText::jsonb, @summary::jsonb, @rowCount, @receivedAt, NOW())
            ON CONFLICT (universe) DO UPDATE
                SET payload = EXCLUDED.payload,
                    summary = EXCLUDED.summary,
                    row_count = EXCLUDED.row_count,
                    received_at_utc = EXCLUDED.received_at_utc,
                    updated_at = NOW();",
            new { universe, payloadText, summary, rowCount, receivedAt });

        return new CompareEnvelope(
            Universe: universe,
            RunId: runId,
            GeneratedAtUtc: generatedAt,
            ReceivedAtUtc: receivedAt,
            RankMetric: rankMetric,
            RowCount: rowCount,
            Payload: payload.Clone());
    }

    public CompareEnvelope? GetLatest(string universe)
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<CompareRow>(@"
            SELECT universe, row_count, received_at_utc, summary::text AS summary, payload::text AS payload
            FROM compare_cache WHERE universe = @universe;",
            new { universe });
        if (row is null) return null;
        var summary = JsonbHelpers.FromJsonb(row.summary);
        return new CompareEnvelope(
            Universe: row.universe,
            RunId: JsonbHelpers.ReadString(summary, "run_id"),
            GeneratedAtUtc: JsonbHelpers.ReadDateOrNull(summary, "generated_at") ?? row.received_at_utc,
            ReceivedAtUtc: row.received_at_utc,
            RankMetric: JsonbHelpers.ReadString(summary, "rank_metric"),
            RowCount: row.row_count,
            Payload: JsonbHelpers.FromJsonb(row.payload));
    }

    public IReadOnlyList<CompareSummary> ListUniverses()
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<CompareSummaryRow>(@"
            SELECT universe, row_count, received_at_utc, summary::text AS summary
            FROM compare_cache
            ORDER BY received_at_utc DESC;").ToList();
        return rows.Select(r =>
        {
            var summary = JsonbHelpers.FromJsonb(r.summary);
            return new CompareSummary(
                Universe: r.universe,
                RunId: JsonbHelpers.ReadString(summary, "run_id"),
                GeneratedAtUtc: JsonbHelpers.ReadDateOrNull(summary, "generated_at") ?? r.received_at_utc,
                ReceivedAtUtc: r.received_at_utc,
                RankMetric: JsonbHelpers.ReadString(summary, "rank_metric"),
                RowCount: r.row_count);
        }).ToArray();
    }

    private sealed record CompareRow(string universe, int row_count, DateTime received_at_utc, string summary, string payload);
    private sealed record CompareSummaryRow(string universe, int row_count, DateTime received_at_utc, string summary);
}
