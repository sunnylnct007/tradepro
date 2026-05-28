using System.Text.Json;

namespace TradePro.Api.Models;

/// Metadata extracted from an incoming compare payload. The full Python
/// JSON is preserved verbatim in `Payload` so the frontend renders the
/// exact thing the Mac produced; this envelope is just enough to index
/// and list past runs.
public record CompareEnvelope(
    string Universe,
    string? RunId,
    DateTime GeneratedAtUtc,
    DateTime ReceivedAtUtc,
    string? RankMetric,
    int RowCount,
    JsonElement Payload);

public record CompareSummary(
    string Universe,
    string? RunId,
    DateTime GeneratedAtUtc,
    DateTime ReceivedAtUtc,
    string? RankMetric,
    int RowCount);
