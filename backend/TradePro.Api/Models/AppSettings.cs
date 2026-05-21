namespace TradePro.Api.Models;

/// User-editable runtime configuration. Read by the Mac comparator at
/// the start of each run + by the frontend on the Settings page.
///
/// Strict policy: every field MUST have a sane default so a fresh
/// install (or a settings file that's missing fields after an upgrade)
/// behaves like the compiled defaults. The Mac comparator uses these
/// values verbatim — if a field is wrong, fix it here, don't redeploy
/// the Python package.
public record AppSettings(
    // Sentiment-driven BUY → WAIT demotion. Mirrors the compiled
    // defaults in strategies/tradepro_strategies/compare.py.
    SentimentSettings Sentiment,
    DateTime UpdatedAtUtc,
    // Paper-trading order placement. The Mac engine reads this at
    // session start and uses it as the default for --placement-mode.
    // CLI flag still wins for ad-hoc overrides. Nullable on the wire
    // so legacy PUT payloads without this block still work; the API
    // fills it in from the current row before persisting.
    PaperSettings? Paper = null,
    // Intraday automation knobs (Task #69). Read by the continuous-
    // mode engine on every scan cycle so the user can re-tune live.
    // Nullable for the same backfill reason as Paper.
    IntradaySettings? Intraday = null);

public record SentimentSettings(
    // 7-day mean sentiment ≤ this triggers demotion. Range [-1, 1].
    double MeanSentimentThreshold,
    // AND at least this many material-negative headlines.
    int MinMaterialNegativeCount,
    // Rolling window the rule looks at.
    int LookbackDays);

public record PaperSettings(
    // "auto" | "manual". Auto: T212OrderRouter places orders directly.
    // Manual: router pushes the intent to /api/ingest/paper-pending-order;
    // user clicks Approve/Reject on the Paper page and the API places
    // (or doesn't) on the human's say-so.
    string PlacementMode);

/// Intraday automation block (Task #69). Every threshold is exposed
/// so the user can re-tune without redeploying. Defaults shipped
/// below match the locked-in values from the task description.
public record IntradaySettings(
    // Tickers being watched by the continuous-mode engine.
    string[] Symbols,
    // How often the engine evaluates each symbol (in minutes).
    int ScanIntervalMinutes,
    // Session window — engine sleeps outside these UTC hours.
    // "HH:mm" 24h format.
    string SessionStartUtc,
    string SessionEndUtc,
    // Pre-trade gate — ALL must pass for the order to even be
    // considered. min R:R = reward/risk; max spread = bid/ask
    // spread as % of mid; min confidence = strategy emitter's
    // confidence value in [0,1].
    IntradayGate Gate,
    // Auto-vs-pending router: confidence ≥ this → auto-place
    // (still type-locked to T212 demo); below → queue as Pending.
    double AutoPlaceConfidenceThreshold,
    // USD risk budget per trade — engine sizes positions so a
    // stop-loss hit costs at most this much.
    double RiskPerTradeUsd);

public record IntradayGate(
    double MinRiskRewardRatio,
    double MaxSpreadPct,
    double MinConfidence);

public static class AppSettingsDefaults
{
    public static AppSettings Build() => new(
        Sentiment: new SentimentSettings(
            MeanSentimentThreshold: -0.30,
            MinMaterialNegativeCount: 2,
            LookbackDays: 7),
        Paper: new PaperSettings(
            PlacementMode: "manual"),
        Intraday: new IntradaySettings(
            Symbols: Array.Empty<string>(),
            ScanIntervalMinutes: 1,
            SessionStartUtc: "13:30",   // US market open in UTC (Mar–Nov)
            SessionEndUtc: "20:00",     // US market close
            Gate: new IntradayGate(
                MinRiskRewardRatio: 2.0,
                MaxSpreadPct: 0.3,
                MinConfidence: 0.70),
            AutoPlaceConfidenceThreshold: 0.85,
            RiskPerTradeUsd: 100.0),
        UpdatedAtUtc: DateTime.UtcNow);
}
