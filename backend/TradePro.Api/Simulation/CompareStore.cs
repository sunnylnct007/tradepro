using System.Collections.Concurrent;
using System.Text.Json;
using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

public interface ICompareStore
{
    /// Persist the most recent payload for a universe, replacing any prior
    /// entry. Returns the parsed envelope (with extracted metadata).
    CompareEnvelope Put(JsonElement payload);

    /// Most recent payload for a universe, or null if nothing has been
    /// pushed for that universe yet.
    CompareEnvelope? GetLatest(string universe);

    /// One summary per universe currently held.
    IReadOnlyList<CompareSummary> ListUniverses();
}

/// In-memory most-recent store. One entry per universe — pushing a new
/// compare for the same universe overwrites the prior one. Survives until
/// the App Service worker is recycled. Replace with a Firestore-backed
/// implementation when we need history.
public class InMemoryCompareStore : ICompareStore
{
    private readonly ConcurrentDictionary<string, CompareEnvelope> _byUniverse = new();

    public CompareEnvelope Put(JsonElement payload)
    {
        var universe = ReadString(payload, "universe") ?? "custom";
        var runId = ReadString(payload, "run_id");
        var rankMetric = ReadString(payload, "rank_metric");
        var generatedAt = ReadDate(payload, "generated_at") ?? DateTime.UtcNow;
        var rowCount = ReadArrayLength(payload, "rows");

        var envelope = new CompareEnvelope(
            Universe: universe,
            RunId: runId,
            GeneratedAtUtc: generatedAt,
            ReceivedAtUtc: DateTime.UtcNow,
            RankMetric: rankMetric,
            RowCount: rowCount,
            // Clone so the JsonDocument backing the request body can be disposed
            // by the framework without invalidating our stored payload.
            Payload: payload.Clone());

        _byUniverse[universe] = envelope;
        return envelope;
    }

    public CompareEnvelope? GetLatest(string universe)
        => _byUniverse.TryGetValue(universe, out var env) ? env : null;

    public IReadOnlyList<CompareSummary> ListUniverses()
        => _byUniverse.Values
            .Select(e => new CompareSummary(
                e.Universe, e.RunId, e.GeneratedAtUtc, e.ReceivedAtUtc,
                e.RankMetric, e.RowCount))
            .OrderByDescending(s => s.GeneratedAtUtc)
            .ToArray();

    private static string? ReadString(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static DateTime? ReadDate(JsonElement el, string key)
    {
        if (!el.TryGetProperty(key, out var v) || v.ValueKind != JsonValueKind.String)
            return null;
        var s = v.GetString();
        if (string.IsNullOrEmpty(s)) return null;
        return DateTime.TryParse(s, null, System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out var dt)
            ? dt
            : null;
    }

    private static int ReadArrayLength(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Array
            ? v.GetArrayLength()
            : 0;
}
