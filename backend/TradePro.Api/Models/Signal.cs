namespace TradePro.Api.Models;

public record SignalRequest(
    string Symbol,
    string? Provider,
    string Strategy,
    int LookbackDays,
    Dictionary<string, double>? Params);

public record IndicatorSnapshot(
    decimal? Sma20,
    decimal? Sma50,
    decimal? Sma200,
    decimal? Rsi14,
    decimal? LastClose,
    decimal? PriceVs52wHighPct,
    decimal? PriceVs52wLowPct);

public record SignalDecision(
    string Symbol,
    string Strategy,
    DateTime AsOf,
    string Action,           // "BUY" | "SELL" | "HOLD"
    double Confidence,       // 0..1 rough score
    IReadOnlyList<string> Reasons,
    IndicatorSnapshot Indicators,
    decimal? SuggestedStopLossPct,
    decimal? SuggestedTargetPct);

public record ScanRequest(
    string? Watchlist,        // named list (e.g. "uk"); optional if Symbols is set
    string[]? Symbols,        // ad-hoc list — takes precedence over Watchlist when set
    string? Provider,
    string Strategy,
    Dictionary<string, double>? Params);

public record ScanResultItem(
    string Symbol,
    string Label,
    SignalDecision Decision);

public record ScanResult(
    string Watchlist,
    string Strategy,
    DateTime GeneratedAt,
    IReadOnlyList<ScanResultItem> Buys,
    IReadOnlyList<ScanResultItem> Sells,
    IReadOnlyList<ScanResultItem> Holds,
    IReadOnlyList<string> Errors);
