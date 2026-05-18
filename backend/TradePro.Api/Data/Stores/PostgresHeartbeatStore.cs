using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Models;
using TradePro.Api.Simulation;

namespace TradePro.Api.Data.Stores;

/// <summary>
/// Postgres-backed heartbeat store. Single host today (sentinel
/// `host` column doubles as primary key) — when we go multi-tenant
/// the same schema gives us per-host history with no migration.
///
/// We keep the full envelope materialised in JSONB and only project
/// the fields the API actually returns. Read path doesn't have to
/// deserialise into HeartbeatEnvelope-shaped JSON every time; the
/// JsonElement is reconstructed lazily.
/// </summary>
public sealed class PostgresHeartbeatStore : IHeartbeatStore
{
    private readonly NpgsqlDataSource _db;

    public PostgresHeartbeatStore(NpgsqlDataSource db)
    {
        _db = db;
    }

    public HeartbeatEnvelope Put(JsonElement payload)
    {
        var host = JsonbHelpers.ReadString(payload, "host") ?? "unknown";
        var gitSha = JsonbHelpers.ReadString(payload, "git_sha");
        var sent = JsonbHelpers.ReadDateOrNull(payload, "sent_at") ?? DateTime.UtcNow;
        var uptime = JsonbHelpers.ReadIntOrNull(payload, "uptime_seconds");

        string? currentTask = null, taskDetail = null, taskPhase = null;
        DateTime? taskStarted = null;
        if (payload.ValueKind == JsonValueKind.Object
            && payload.TryGetProperty("current_task", out var ct)
            && ct.ValueKind == JsonValueKind.Object)
        {
            currentTask = JsonbHelpers.ReadString(ct, "task");
            taskDetail = JsonbHelpers.ReadString(ct, "detail");
            taskPhase = JsonbHelpers.ReadString(ct, "phase");
            taskStarted = JsonbHelpers.ReadDateOrNull(ct, "started_at");
        }

        var receivedAt = DateTime.UtcNow;
        var jsonText = JsonbHelpers.ToJsonb(payload);

        using var conn = _db.OpenConnection();
        conn.Execute(@"
            INSERT INTO heartbeats (host, payload, last_seen_at)
            VALUES (@host, @payload::jsonb, @last_seen_at)
            ON CONFLICT (host) DO UPDATE
                SET payload = EXCLUDED.payload,
                    last_seen_at = EXCLUDED.last_seen_at;",
            new { host, payload = jsonText, last_seen_at = receivedAt });

        return new HeartbeatEnvelope(
            Host: host,
            GitSha: gitSha,
            SentAtUtc: sent,
            ReceivedAtUtc: receivedAt,
            UptimeSeconds: uptime,
            CurrentTask: currentTask,
            CurrentTaskDetail: taskDetail,
            CurrentTaskPhase: taskPhase,
            CurrentTaskStartedAt: taskStarted,
            Payload: payload.Clone());
    }

    public HeartbeatEnvelope? GetLatest()
    {
        using var conn = _db.OpenConnection();
        var row = conn.QueryFirstOrDefault<(string Host, string Payload, DateTime LastSeenAt)>(@"
            SELECT host, payload::text AS payload, last_seen_at
            FROM heartbeats
            ORDER BY last_seen_at DESC
            LIMIT 1");
        if (row.Host is null) return null;
        var el = JsonbHelpers.FromJsonb(row.Payload);
        return new HeartbeatEnvelope(
            Host: row.Host,
            GitSha: JsonbHelpers.ReadString(el, "git_sha"),
            SentAtUtc: JsonbHelpers.ReadDateOrNull(el, "sent_at") ?? row.LastSeenAt,
            ReceivedAtUtc: row.LastSeenAt,
            UptimeSeconds: JsonbHelpers.ReadIntOrNull(el, "uptime_seconds"),
            CurrentTask: el.TryGetProperty("current_task", out var ct) && ct.ValueKind == JsonValueKind.Object
                ? JsonbHelpers.ReadString(ct, "task") : null,
            CurrentTaskDetail: ct.ValueKind == JsonValueKind.Object
                ? JsonbHelpers.ReadString(ct, "detail") : null,
            CurrentTaskPhase: ct.ValueKind == JsonValueKind.Object
                ? JsonbHelpers.ReadString(ct, "phase") : null,
            CurrentTaskStartedAt: ct.ValueKind == JsonValueKind.Object
                ? JsonbHelpers.ReadDateOrNull(ct, "started_at") : null,
            Payload: el);
    }
}
