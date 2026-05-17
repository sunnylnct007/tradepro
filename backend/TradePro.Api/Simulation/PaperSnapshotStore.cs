using System.Collections.Concurrent;
using System.Text.Json;

namespace TradePro.Api.Simulation;

/// <summary>
/// Stores paper-trading ledger snapshots pushed from the Mac at the
/// end of every <c>tradepro-paper --push</c> session. The frontend
/// "Live" tab on the Paper page reads these to show recent fills +
/// open positions per strategy.
///
/// Key shape: one snapshot per session_label (e.g. "AAPL-2026-05-15").
/// Re-pushing the same session_label overwrites the prior — typical
/// because a fresh run of the same session through the engine is
/// authoritative over the previous attempt.
///
/// In-memory + capped at 100 snapshots so a Mac that runs a lot of
/// quick sessions doesn't pile up garbage. Oldest snapshots evict
/// when the cap is hit, keyed by ReceivedAtUtc.
/// </summary>
public interface IPaperSnapshotStore
{
    PaperSnapshotEnvelope Put(JsonElement payload);
    PaperSnapshotEnvelope? Get(string sessionLabel);
    IReadOnlyList<PaperSnapshotSummary> List(int limit = 50);
}

public sealed record PaperSnapshotEnvelope(
    string SessionLabel,
    string Broker,
    string AsOfUtc,
    int StrategyCount,
    int TotalFills,
    DateTime ReceivedAtUtc,
    JsonElement Payload);

public sealed record PaperSnapshotSummary(
    string SessionLabel,
    string Broker,
    string AsOfUtc,
    int StrategyCount,
    int TotalFills,
    DateTime ReceivedAtUtc);

public sealed class InMemoryPaperSnapshotStore : IPaperSnapshotStore
{
    private const int MaxSnapshots = 100;
    private readonly ConcurrentDictionary<string, PaperSnapshotEnvelope> _byLabel = new();

    public PaperSnapshotEnvelope Put(JsonElement payload)
    {
        var sessionLabel = ReadString(payload, "session_label") ?? "(no-label)";
        var broker = ReadString(payload, "broker") ?? "?";
        var asOf = ReadString(payload, "as_of_utc") ?? DateTime.UtcNow.ToString("o");
        var strategyCount = 0;
        var totalFills = 0;
        if (payload.TryGetProperty("strategies", out var sArr)
            && sArr.ValueKind == JsonValueKind.Array)
        {
            strategyCount = sArr.GetArrayLength();
            foreach (var s in sArr.EnumerateArray())
            {
                if (s.TryGetProperty("fills_count", out var f)
                    && f.ValueKind == JsonValueKind.Number)
                {
                    totalFills += f.GetInt32();
                }
            }
        }

        var envelope = new PaperSnapshotEnvelope(
            SessionLabel: sessionLabel,
            Broker: broker,
            AsOfUtc: asOf,
            StrategyCount: strategyCount,
            TotalFills: totalFills,
            ReceivedAtUtc: DateTime.UtcNow,
            // Clone so the framework can dispose the request-backing JsonDocument.
            Payload: payload.Clone());

        _byLabel[sessionLabel] = envelope;
        // Evict oldest if we're over the cap. Cheap: only fires when
        // count crosses the threshold; not optimised for huge N.
        if (_byLabel.Count > MaxSnapshots)
        {
            var oldest = _byLabel
                .OrderBy(kv => kv.Value.ReceivedAtUtc)
                .First();
            _byLabel.TryRemove(oldest.Key, out _);
        }
        return envelope;
    }

    public PaperSnapshotEnvelope? Get(string sessionLabel)
        => _byLabel.TryGetValue(sessionLabel, out var env) ? env : null;

    public IReadOnlyList<PaperSnapshotSummary> List(int limit = 50)
        => _byLabel.Values
            .OrderByDescending(e => e.ReceivedAtUtc)
            .Take(limit)
            .Select(e => new PaperSnapshotSummary(
                e.SessionLabel, e.Broker, e.AsOfUtc,
                e.StrategyCount, e.TotalFills, e.ReceivedAtUtc))
            .ToArray();

    private static string? ReadString(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;
}
