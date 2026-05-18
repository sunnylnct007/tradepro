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
    PaperSettings? Paper = null);

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

public static class AppSettingsDefaults
{
    public static AppSettings Build() => new(
        Sentiment: new SentimentSettings(
            MeanSentimentThreshold: -0.30,
            MinMaterialNegativeCount: 2,
            LookbackDays: 7),
        Paper: new PaperSettings(
            PlacementMode: "manual"),
        UpdatedAtUtc: DateTime.UtcNow);
}
