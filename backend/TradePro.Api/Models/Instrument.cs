namespace TradePro.Api.Models;

/// <summary>
/// One row in the symbol picker. The symbol field is whatever the
/// upstream price feed expects (today: Yahoo Finance) — the Compare
/// + Signals pages can pass it straight to <c>/api/marketdata/candles</c>
/// without any further mapping.
/// </summary>
public sealed record InstrumentMatch(
    string Symbol,
    string Name,
    string? Exchange,
    string? Type,
    string? Currency,
    string Source);

public sealed record InstrumentSearchResponse(
    string Query,
    int Count,
    IReadOnlyList<InstrumentMatch> Items);
