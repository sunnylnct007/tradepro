namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Bound to the "Trading212" config section. Secrets (ApiKey, ApiSecret)
/// should come from environment variables, not committed appsettings.
///
/// Mode picks which T212 base URL we hit:
///   "demo"     → https://demo.trading212.com/api/v0   (paper trading)
///   "live"     → https://live.trading212.com/api/v0   (real money)
///   "disabled" → integration off; client throws on any call
///
/// Default is "disabled" so an unconfigured deployment never accidentally
/// reaches the live endpoint. Auth is Basic(API_KEY:API_SECRET) per
/// https://t212public-api-docs.redoc.ly.
/// </summary>
public sealed class Trading212Options
{
    public const string SectionName = "Trading212";

    public string Mode { get; set; } = "disabled";
    public string ApiKey { get; set; } = string.Empty;
    public string ApiSecret { get; set; } = string.Empty;
    public int TimeoutSeconds { get; set; } = 15;

    public bool IsEnabled =>
        !string.Equals(Mode, "disabled", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(ApiKey)
        && !string.IsNullOrWhiteSpace(ApiSecret);

    public string BaseUrl => Mode.ToLowerInvariant() switch
    {
        "live" => "https://live.trading212.com/api/v0/",
        "demo" => "https://demo.trading212.com/api/v0/",
        _ => "https://demo.trading212.com/api/v0/",
    };
}
