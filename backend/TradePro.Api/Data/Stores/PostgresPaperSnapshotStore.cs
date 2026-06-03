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

    /// <summary>
    /// Clean unrealised P&L for a strategy, dropping the contribution of any
    /// position whose avg_entry_price is missing/zero. A cost-basis-0 position
    /// computes unrealised as (mark − 0) × qty = the position NOTIONAL (~$27k),
    /// not P&L — which poisoned the P&L curve (a $50k desk read +$27k). We
    /// can't compute real P&L without a cost basis, so such a position
    /// contributes 0. Only overrides the strategy-reported scalar when a
    /// cost-basis-0 position is actually present (so FX price-unit semantics
    /// and normal snapshots are untouched). Falls back to <paramref name="fallback"/>
    /// when there's no positions array to recompute from.
    /// </summary>
    private static double CleanUnrealised(JsonElement s, double fallback)
    {
        if (!s.TryGetProperty("positions", out var pos) || pos.ValueKind != JsonValueKind.Array)
            return fallback;
        var sum = 0.0;
        var sawCostBasisZero = false;
        var any = false;
        foreach (var p in pos.EnumerateArray())
        {
            any = true;
            var avg = JsonbHelpers.ReadDoubleOrNull(p, "avg_entry_price") ?? 0.0;
            if (avg > 0)
                sum += JsonbHelpers.ReadDoubleOrNull(p, "unrealised_pnl") ?? 0.0;
            else
                sawCostBasisZero = true;  // drop its notional-as-P&L contribution
        }
        return (any && sawCostBasisZero) ? sum : fallback;
    }

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

        // Append per-strategy P&L points for the intraday cockpit curve.
        // paper_sessions only keeps the day's LATEST snapshot (UPSERT), so
        // without this the intraday shape is lost. Best-effort — a points
        // write must never fail the snapshot ingest.
        if (strategiesEl.ValueKind == JsonValueKind.Array)
        {
            try
            {
                foreach (var s in strategiesEl.EnumerateArray())
                {
                    var sid = JsonbHelpers.ReadString(s, "strategy_id");
                    if (string.IsNullOrWhiteSpace(sid)) continue;
                    conn.Execute(@"
                        INSERT INTO paper_pnl_points
                            (strategy_id, broker, as_of_utc, realised_pnl, unrealised_pnl, equity)
                        VALUES (@sid, @broker, @asOfTs, @realised, @unrealised, @equity);",
                        new
                        {
                            sid,
                            broker,
                            asOfTs,
                            realised = JsonbHelpers.ReadDoubleOrNull(s, "realised_pnl") ?? 0.0,
                            unrealised = CleanUnrealised(s, JsonbHelpers.ReadDoubleOrNull(s, "unrealised_pnl") ?? 0.0),
                            equity = JsonbHelpers.ReadDoubleOrNull(s, "equity") ?? 0.0,
                        });
                }
            }
            catch (Exception ex)
            {
                // Non-fatal: the snapshot (and its daily row) already landed.
                System.Diagnostics.Debug.WriteLine($"paper_pnl_points insert failed: {ex.Message}");
            }
        }

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

    public IReadOnlyList<PnlStrategySeries> PnlSeries(string scope)
    {
        using var conn = _db.OpenConnection();
        if (scope == "intraday")
        {
            // Real intraday shape from the append-only points table (today).
            var pts = conn.Query<PnlPointRow>(@"
                SELECT strategy_id, as_of_utc, realised_pnl, unrealised_pnl, equity
                FROM paper_pnl_points
                WHERE as_of_utc >= date_trunc('day', NOW())
                ORDER BY strategy_id, as_of_utc ASC;").ToList();
            return pts
                .GroupBy(p => p.strategy_id)
                .Select(g => new PnlStrategySeries(
                    g.Key,
                    g.Select(p => new PnlPoint(
                        p.as_of_utc.ToString("o"), p.realised_pnl, p.unrealised_pnl,
                        p.equity, p.realised_pnl + p.unrealised_pnl)).ToArray()))
                .OrderBy(s => s.StrategyId)
                .ToArray();
        }

        // Daily (all-time): one point per (strategy, day) from the session
        // snapshots. session_label is "{strategy}-{date}", one row per day,
        // so we parse each payload's per-strategy P&L. Cap to a year.
        var rows = conn.Query<SnapshotRow>(@"
            SELECT session_label, broker, as_of_utc, strategy_count, total_fills, received_at_utc, payload::text AS payload
            FROM paper_sessions
            WHERE as_of_utc >= NOW() - INTERVAL '365 days'
            ORDER BY as_of_utc ASC;").ToList();

        var byStrategy = new Dictionary<string, Dictionary<string, PnlPoint>>();
        foreach (var row in rows)
        {
            var day = row.as_of_utc.ToString("yyyy-MM-dd");
            JsonElement payload;
            try { payload = JsonbHelpers.FromJsonb(row.payload); }
            catch { continue; }
            if (!payload.TryGetProperty("strategies", out var arr)
                || arr.ValueKind != JsonValueKind.Array) continue;
            foreach (var s in arr.EnumerateArray())
            {
                var sid = JsonbHelpers.ReadString(s, "strategy_id");
                if (string.IsNullOrWhiteSpace(sid)) continue;
                var r = JsonbHelpers.ReadDoubleOrNull(s, "realised_pnl") ?? 0.0;
                // Recompute clean unrealised from positions, dropping cost-basis-0
                // (notional-as-P&L) at READ time — repairs historical snapshots too
                // (the daily series parses the raw payload, NOT paper_pnl_points, so
                // the ingest-side guard + migration 039 don't reach this path).
                var u = CleanUnrealised(s, JsonbHelpers.ReadDoubleOrNull(s, "unrealised_pnl") ?? 0.0);
                var e = JsonbHelpers.ReadDoubleOrNull(s, "equity") ?? 0.0;
                if (!byStrategy.TryGetValue(sid, out var pts)) byStrategy[sid] = pts = new();
                pts[day] = new PnlPoint(day, r, u, e, r + u);  // latest snapshot of the day wins
            }
        }
        return byStrategy
            .Select(kv => new PnlStrategySeries(
                kv.Key, kv.Value.Values.OrderBy(p => p.Ts).ToArray()))
            .OrderBy(s => s.StrategyId)
            .ToArray();
    }

    private sealed record PnlPointRow(
        string strategy_id, DateTime as_of_utc,
        double realised_pnl, double unrealised_pnl, double equity);

    private sealed record SnapshotRow(
        string session_label, string broker, DateTime as_of_utc,
        int strategy_count, int total_fills, DateTime received_at_utc, string payload);

    private sealed record SnapshotSummaryRow(
        string session_label, string broker, DateTime as_of_utc,
        int strategy_count, int total_fills, DateTime received_at_utc);
}
