using System.Collections.Concurrent;
using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

public interface IHeartbeatStore
{
    /// Persist the most recent ping for a host. Returns the parsed envelope.
    HeartbeatEnvelope Put(JsonElement payload);

    /// The single host's most recent ping, or null if nothing has pinged.
    /// (Single-user assumption A1 — one Mac per deployment. Generalise to
    /// per-host map when we go multi-tenant.)
    HeartbeatEnvelope? GetLatest();
}

/// In-memory store. Heartbeats are ephemeral: if the API restarts, the
/// next heartbeat lands within ~15 min anyway. No need to persist them.
/// The compare cache (long-lived) is on disk; this isn't.
public sealed class InMemoryHeartbeatStore : IHeartbeatStore
{
    private HeartbeatEnvelope? _latest;
    private readonly object _lock = new();

    public HeartbeatEnvelope Put(JsonElement payload)
    {
        var host = ReadString(payload, "host") ?? "unknown";
        var gitSha = ReadString(payload, "git_sha");
        var sent = ReadDate(payload, "sent_at") ?? DateTime.UtcNow;
        var uptime = ReadInt(payload, "uptime_seconds");

        // current_task is either null (idle) or {task, detail, phase, started_at, ...}
        string? currentTask = null, taskDetail = null, taskPhase = null;
        DateTime? taskStarted = null;
        if (payload.TryGetProperty("current_task", out var ct)
            && ct.ValueKind == JsonValueKind.Object)
        {
            currentTask = ReadString(ct, "task");
            taskDetail = ReadString(ct, "detail");
            taskPhase = ReadString(ct, "phase");
            taskStarted = ReadDate(ct, "started_at");
        }

        var envelope = new HeartbeatEnvelope(
            Host: host,
            GitSha: gitSha,
            SentAtUtc: sent,
            ReceivedAtUtc: DateTime.UtcNow,
            UptimeSeconds: uptime,
            CurrentTask: currentTask,
            CurrentTaskDetail: taskDetail,
            CurrentTaskPhase: taskPhase,
            CurrentTaskStartedAt: taskStarted,
            Payload: payload.Clone());

        lock (_lock) _latest = envelope;
        return envelope;
    }

    public HeartbeatEnvelope? GetLatest()
    {
        lock (_lock) return _latest;
    }

    private static string? ReadString(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static int? ReadInt(JsonElement el, string key)
        => el.ValueKind == JsonValueKind.Object
           && el.TryGetProperty(key, out var v)
           && v.ValueKind == JsonValueKind.Number
           && v.TryGetInt32(out var n)
            ? n
            : null;

    private static DateTime? ReadDate(JsonElement el, string key)
    {
        if (el.ValueKind != JsonValueKind.Object) return null;
        if (!el.TryGetProperty(key, out var v) || v.ValueKind != JsonValueKind.String) return null;
        var s = v.GetString();
        if (string.IsNullOrEmpty(s)) return null;
        return DateTime.TryParse(s, null,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal,
            out var dt) ? dt : null;
    }
}
