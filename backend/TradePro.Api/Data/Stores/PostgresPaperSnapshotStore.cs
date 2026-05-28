using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Per-session ledger snapshots. <c>session_label</c> is the natural
/// key (typically "{SYMBOL}-{YYYY-MM-DD}" but anything stable works).
/// Pushing the same label twice overwrites — the Mac may push at
/// session-end and again on shutdown; the later snapshot wins.
///
/// The summary columns (broker, strategy_count, total_fills,
/// as_of_utc) are denormalised from the payload so List() doesn't
/// pay JSONB-parse cost per row.
/// </summary>
public sealed class PostgresPaperSnapshotStore : IPaperSnapshotStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresPaperSnapshotStore(NpgsqlDataSource db) { _db = db; }

    public PaperSnapshotEnvelope Put(JsonElement payload)
    {
        var sessionLabel = JsonbHelpers.ReadString(payload, "session_label")
            ?? throw new ArgumentException("payload missing session_label", nameof(payload));
        var broker = JsonbHelpers.ReadString(payload, "broker") ?? "?";
        var asOfUtc = JsonbHelpers.ReadString(payload, "as_of_utc")
            ?? DateTime.UtcNow.ToString("o");
        var asOfTs = DateTime.TryParse(asOfUtc, null,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal,
            out var ts) ? ts : DateTime.UtcNow;
        var strategyCount = JsonbHelpers.ReadArrayLength(payload, "strategies");
        var totalFills = 0;
        if (payload.TryGetProperty("strategies", out var strategiesEl)
            && strategiesEl.ValueKind == JsonValueKind.Array)
        {
            foreach (var s in strategiesEl.EnumerateArray())
            {
                totalFills += JsonbHelpers.ReadInt(s, "fills_count");
            }
        }

        var receivedAt = DateTime.UtcNow;
        var jsonText = JsonbHelpers.ToJsonb(payload);

        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO paper_sessions (session_label, broker, as_of_utc, strategy_count, total_fills, payload, received_at_utc)
            VALUES (@sessionLabel, @broker, @asOfTs, @strategyCount, @totalFills, @payload::jsonb, @receivedAt)
            ON CONFLICT (session_label) DO UPDATE
                SET broker = EXCLUDED.broker,
                    as_of_utc = EXCLUDED.as_of_utc,
                    strategy_count = EXCLUDED.strategy_count,
                    total_fills = EXCLUDED.total_fills,
                    payload = EXCLUDED.payload,
                    received_at_utc = EXCLUDED.received_at_utc;",
            new { sessionLabel, broker, asOfTs, strategyCount, totalFills, payload = jsonText, receivedAt });

        return new PaperSnapshotEnvelope(
            SessionLabel: sessionLabel,
            Broker: broker,
            AsOfUtc: asOfUtc,
            StrategyCount: strategyCount,
            TotalFills: totalFills,
            ReceivedAtUtc: receivedAt,
            Payload: payload.Clone());
    }

    public PaperSnapshotEnvelope? Get(string sessionLabel)
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<SnapshotRow>(@"
            SELECT session_label, broker, as_of_utc, strategy_count, total_fills, received_at_utc, payload::text AS payload
            FROM paper_sessions WHERE session_label = @sessionLabel;",
            new { sessionLabel });
        if (row is null) return null;
        return new PaperSnapshotEnvelope(
            SessionLabel: row.session_label,
            Broker: row.broker,
            AsOfUtc: row.as_of_utc.ToString("o"),
            StrategyCount: row.strategy_count,
            TotalFills: row.total_fills,
            ReceivedAtUtc: row.received_at_utc,
            Payload: JsonbHelpers.FromJsonb(row.payload));
    }

    public IReadOnlyList<PaperSnapshotSummary> List(int limit = 50)
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<SnapshotSummaryRow>(@"
            SELECT session_label, broker, as_of_utc, strategy_count, total_fills, received_at_utc
            FROM paper_sessions
            ORDER BY received_at_utc DESC
            LIMIT @limit;",
            new { limit }).ToList();
        return rows.Select(r => new PaperSnapshotSummary(
            SessionLabel: r.session_label,
            Broker: r.broker,
            AsOfUtc: r.as_of_utc.ToString("o"),
            StrategyCount: r.strategy_count,
            TotalFills: r.total_fills,
            ReceivedAtUtc: r.received_at_utc)).ToArray();
    }

    private sealed record SnapshotRow(
        string session_label, string broker, DateTime as_of_utc,
        int strategy_count, int total_fills, DateTime received_at_utc, string payload);

    private sealed record SnapshotSummaryRow(
        string session_label, string broker, DateTime as_of_utc,
        int strategy_count, int total_fills, DateTime received_at_utc);
}
