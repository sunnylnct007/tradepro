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
    DateTime UpdatedAtUtc);

public record SentimentSettings(
    // 7-day mean sentiment ≤ this triggers demotion. Range [-1, 1].
    double MeanSentimentThreshold,
    // AND at least this many material-negative headlines.
    int MinMaterialNegativeCount,
    // Rolling window the rule looks at.
    int LookbackDays);

public static class AppSettingsDefaults
{
    public static AppSettings Build() => new(
        Sentiment: new SentimentSettings(
            MeanSentimentThreshold: -0.30,
            MinMaterialNegativeCount: 2,
            LookbackDays: 7),
        UpdatedAtUtc: DateTime.UtcNow);
}
