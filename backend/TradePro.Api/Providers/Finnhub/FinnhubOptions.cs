namespace TradePro.Api.Providers.Finnhub;

/// <summary>
/// Bound to the "Finnhub" config section. ApiKey comes from env in
/// production (Finnhub__ApiKey). Free tier: 60 req/min, sufficient
/// for the earnings-calendar endpoint we use.
///
/// Off by default so an unconfigured deployment doesn't accidentally
/// leak symbols to a third party. Only when a key is set does the
/// integration actually call out.
/// </summary>
public sealed class FinnhubOptions
{
    public const string SectionName = "Finnhub";

    public string ApiKey { get; set; } = string.Empty;
    public string BaseUrl { get; set; } = "https://finnhub.io/api/v1/";
    public int TimeoutSeconds { get; set; } = 10;

    public bool IsEnabled => !string.IsNullOrWhiteSpace(ApiKey);
}
