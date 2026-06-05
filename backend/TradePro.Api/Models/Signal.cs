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
    decimal? SuggestedTargetPct,
    // Position STATE — separate from Action (which is today's TRADE).
    // HOLD action + InPosition=true means "stay long, no fresh signal";
    // HOLD action + InPosition=false means "stay flat, no fresh signal".
    // The Decide page's "N of 7 strategies currently long" count is the
    // sum of in_position across strategies — surface here so Research
    // can reconcile with the same number.
    bool InPosition = false,
    DateTime? PositionSince = null,
    // Phase C-3 — catalyst overlay attached after the technical
    // verdict computes. Always present (empty list when nothing
    // active); never silently overrides Action. Frontend renders
    // these as severity-coloured chips on the decision row.
    IReadOnlyList<CatalystSummary>? Catalysts = null,
    // CatalystFlag = a coarse divergence/alignment signal for the
    // cockpit to chip / banner. Null = neutral / no notable event.
    //   "tech_event_divergence" — Action=HOLD + a HIGH-severity
    //                              catalyst is imminent. The
    //                              Ecopetrol pattern.
    //   "tech_event_alignment"  — Action=BUY + a HIGH-severity
    //                              catalyst supports the call.
    string? CatalystFlag = null);

/// <summary>Phase C-3 — compact catalyst representation embedded
/// on SignalDecision. Same id maps back to /api/catalysts/ so the
/// frontend can drill into the full row + payload + extractor
/// rationale.</summary>
public record CatalystSummary(
    long Id,
    string Kind,
    string? OccursOn,      // YYYY-MM-DD or null when undated
    string Title,
    string Severity,       // "low" | "medium" | "high"
    string Source,
    int? DaysUntil);       // null when OccursOn is null

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
