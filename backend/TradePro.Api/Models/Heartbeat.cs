using System.Text.Json;

namespace TradePro.Api.Models;

/// One per Mac (today: a single Mac, hence a single record). Represents
/// the most recent ping the box sent + the metadata extracted from it.
/// The full payload is preserved verbatim so the UI can drill into
/// last-refresh stats / log tail without re-parsing on the server.
public record HeartbeatEnvelope(
    string Host,
    string? GitSha,
    DateTime SentAtUtc,
    DateTime ReceivedAtUtc,
    int? UptimeSeconds,
    string? CurrentTask,            // null when idle
    string? CurrentTaskDetail,
    string? CurrentTaskPhase,
    DateTime? CurrentTaskStartedAt,
    JsonElement Payload);

/// Liveness verdict derived at GET time, not stored.
public enum WorkerLiveness
{
    Down,    // never seen, or last ping > 24h ago
    Late,    // ping > 30 min ago but ≤ 24h
    Alive,   // ping ≤ 30 min ago
}
