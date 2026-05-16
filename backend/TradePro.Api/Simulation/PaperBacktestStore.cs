using System.Collections.Concurrent;
using System.Text.Json;

namespace TradePro.Api.Simulation;

/// <summary>
/// Stores paper-trading backtest reports pushed from the Mac
/// (`tradepro-paper-compare --push` / `tradepro-paper-backtest --push`).
///
/// Key shape: one report per (symbol, kind, runId). The frontend
/// Backtest page lists reports newest-first and lets the user open one
/// to see the full comparator scoreboard / walk-forward equity curve.
/// In-memory only today — replace with a file/Firestore impl when
/// history matters across restarts. For an MVP "I want to see my
/// last 20 backtests" experience this is fine.
/// </summary>
public interface IPaperBacktestStore
{
    PaperBacktestEnvelope Put(JsonElement payload);
    PaperBacktestEnvelope? Get(string reportId);
    IReadOnlyList<PaperBacktestSummary> List(int limit = 50);
}

public sealed record PaperBacktestEnvelope(
    string ReportId,
    string Kind,            // "compare" | "backtest"
    string Symbol,
    string? Start,
    string? End,
    int EntryCount,
    DateTime ReceivedAtUtc,
    JsonElement Payload);

public sealed record PaperBacktestSummary(
    string ReportId,
    string Kind,
    string Symbol,
    string? Start,
    string? End,
    int EntryCount,
    DateTime ReceivedAtUtc);

public class InMemoryPaperBacktestStore : IPaperBacktestStore
{
    private readonly ConcurrentDictionary<string, PaperBacktestEnvelope> _byId = new();

    public PaperBacktestEnvelope Put(JsonElement payload)
    {
        // The Python comparator emits {symbol, start, end, entries, rankings}.
        // The single-backtest emits the WalkForwardResult.to_summary() shape:
        // {strategy_id, symbol, session_count, ...}. Both shapes are normalised
        // here so the UI gets a uniform envelope to list against.
        var kind = ReadString(payload, "kind")
                   ?? (payload.TryGetProperty("entries", out _) ? "compare" : "backtest");
        var symbol = ReadString(payload, "symbol") ?? "?";
        var start = ReadString(payload, "start");
        var end = ReadString(payload, "end");
        var entryCount = payload.TryGetProperty("entries", out var entries)
                         && entries.ValueKind == JsonValueKind.Array
                            ? entries.GetArrayLength()
                            : 1;
        // Stable id so re-pushing the same params overwrites rather
        // than piling up; falls back to a timestamp if the caller
        // didn't supply one.
        var reportId = ReadString(payload, "report_id")
                       ?? $"{kind}-{symbol}-{start ?? "single"}-{end ?? "single"}-{DateTime.UtcNow.Ticks}";

        var envelope = new PaperBacktestEnvelope(
            ReportId: reportId,
            Kind: kind,
            Symbol: symbol,
            Start: start,
            End: end,
            EntryCount: entryCount,
            ReceivedAtUtc: DateTime.UtcNow,
            // Clone so the framework can dispose the request-backing JsonDocument.
            Payload: payload.Clone());

        _byId[reportId] = envelope;
        return envelope;
    }

    public PaperBacktestEnvelope? Get(string reportId)
        => _byId.TryGetValue(reportId, out var env) ? env : null;

    public IReadOnlyList<PaperBacktestSummary> List(int limit = 50)
        => _byId.Values
            .OrderByDescending(e => e.ReceivedAtUtc)
            .Take(limit)
            .Select(e => new PaperBacktestSummary(
                e.ReportId, e.Kind, e.Symbol, e.Start, e.End,
                e.EntryCount, e.ReceivedAtUtc))
            .ToArray();

    private static string? ReadString(JsonElement el, string key)
        => el.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;
}
