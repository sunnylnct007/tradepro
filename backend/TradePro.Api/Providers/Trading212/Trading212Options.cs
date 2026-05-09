namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Bound to the "Trading212" config section. Secrets (ApiKey) should
/// come from environment variables, not committed appsettings.
///
/// Mode picks which T212 base URL we hit:
///   "demo"     → https://demo.trading212.com/api/v0   (paper trading)
///   "live"     → https://live.trading212.com/api/v0   (real money)
///   "disabled" → integration off; client throws on any call
///
/// Default is "disabled" so an unconfigured deployment never accidentally
/// reaches the live endpoint. Auth is a single API key sent verbatim
/// in the Authorization header (NOT HTTP Basic, NOT key:secret) per
/// https://t212public-api-docs.redoc.ly. ApiSecret is retained for
/// backwards compat with existing .env files but is unused — the
/// public API doesn't have a secret.
/// </summary>
public sealed class Trading212Options
{
    public const string SectionName = "Trading212";

    public string Mode { get; set; } = "disabled";
    public string ApiKey { get; set; } = string.Empty;
    /// <summary>Unused. T212 public API does not have a secret — the
    /// Authorization header carries only the single API key. Kept on
    /// the options class so existing .env files with TRADEPRO_T212_API_SECRET
    /// don't fail to bind.</summary>
    public string ApiSecret { get; set; } = string.Empty;
    public int TimeoutSeconds { get; set; } = 15;

    public bool IsEnabled =>
        !string.Equals(Mode, "disabled", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(ApiKey);

    public string BaseUrl => Mode.ToLowerInvariant() switch
    {
        "live" => "https://live.trading212.com/api/v0/",
        "demo" => "https://demo.trading212.com/api/v0/",
        _ => "https://demo.trading212.com/api/v0/",
    };
}
