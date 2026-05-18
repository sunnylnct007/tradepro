using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Backtest reports pushed by tradepro-paper-backtest. The summary
/// columns are denormalised so the List endpoint never reads the
/// full JSONB payload (those payloads can be hundreds of KB).
/// </summary>
public sealed class PostgresPaperBacktestStore : IPaperBacktestStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresPaperBacktestStore(NpgsqlDataSource db) { _db = db; }

    public PaperBacktestEnvelope Put(JsonElement payload)
    {
        var reportId = JsonbHelpers.ReadString(payload, "report_id") ?? Guid.NewGuid().ToString();
        var kind = JsonbHelpers.ReadString(payload, "kind") ?? "compare";
        var symbol = JsonbHelpers.ReadString(payload, "symbol") ?? "?";
        var start = JsonbHelpers.ReadString(payload, "start");
        var end = JsonbHelpers.ReadString(payload, "end");
        var entryCount = JsonbHelpers.ReadArrayLength(payload, "entries");

        DateTime? startDate = DateTime.TryParse(start, out var sd) ? sd.Date : null;
        DateTime? endDate = DateTime.TryParse(end, out var ed) ? ed.Date : null;
        var receivedAt = DateTime.UtcNow;
        var jsonText = JsonbHelpers.ToJsonb(payload);

        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO paper_backtests (report_id, kind, symbol, start_date, end_date, entry_count, payload, received_at_utc)
            VALUES (@reportId, @kind, @symbol, @startDate, @endDate, @entryCount, @payload::jsonb, @receivedAt)
            ON CONFLICT (report_id) DO UPDATE
                SET kind = EXCLUDED.kind,
                    symbol = EXCLUDED.symbol,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    entry_count = EXCLUDED.entry_count,
                    payload = EXCLUDED.payload,
                    received_at_utc = EXCLUDED.received_at_utc;",
            new { reportId, kind, symbol, startDate, endDate, entryCount, payload = jsonText, receivedAt });

        return new PaperBacktestEnvelope(
            ReportId: reportId,
            Kind: kind,
            Symbol: symbol,
            Start: start,
            End: end,
            EntryCount: entryCount,
            ReceivedAtUtc: receivedAt,
            Payload: payload.Clone());
    }

    public PaperBacktestEnvelope? Get(string reportId)
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<BacktestRow>(@"
            SELECT report_id, kind, symbol, start_date, end_date, entry_count, received_at_utc, payload::text AS payload
            FROM paper_backtests WHERE report_id = @reportId;",
            new { reportId });
        if (row is null) return null;
        return new PaperBacktestEnvelope(
            ReportId: row.report_id,
            Kind: row.kind,
            Symbol: row.symbol,
            Start: row.start_date?.ToString("yyyy-MM-dd"),
            End: row.end_date?.ToString("yyyy-MM-dd"),
            EntryCount: row.entry_count,
            ReceivedAtUtc: row.received_at_utc,
            Payload: JsonbHelpers.FromJsonb(row.payload));
    }

    public IReadOnlyList<PaperBacktestSummary> List(int limit = 50)
    {
        using var conn = _db.OpenConnection();
        var rows = conn.Query<BacktestSummaryRow>(@"
            SELECT report_id, kind, symbol, start_date, end_date, entry_count, received_at_utc
            FROM paper_backtests
            ORDER BY received_at_utc DESC
            LIMIT @limit;",
            new { limit }).ToList();
        return rows.Select(r => new PaperBacktestSummary(
            ReportId: r.report_id,
            Kind: r.kind,
            Symbol: r.symbol,
            Start: r.start_date?.ToString("yyyy-MM-dd"),
            End: r.end_date?.ToString("yyyy-MM-dd"),
            EntryCount: r.entry_count,
            ReceivedAtUtc: r.received_at_utc)).ToArray();
    }

    private sealed record BacktestRow(
        string report_id, string kind, string symbol,
        DateTime? start_date, DateTime? end_date,
        int entry_count, DateTime received_at_utc, string payload);

    private sealed record BacktestSummaryRow(
        string report_id, string kind, string symbol,
        DateTime? start_date, DateTime? end_date,
        int entry_count, DateTime received_at_utc);
}
