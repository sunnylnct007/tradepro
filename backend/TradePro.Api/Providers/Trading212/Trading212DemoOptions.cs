namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Bound to the "Trading212Demo" config section — sibling to
/// <see cref="Trading212Options"/>. Carries the DEMO API key only;
/// the base URL is hard-coded to demo.trading212.com because a "demo
/// client that hits live" defeats the entire point of the split.
///
/// Why two separate options classes instead of a sub-section on
/// Trading212Options? So a misconfiguration on one side cannot leak
/// into the other (e.g. accidentally setting Demo:Mode = "live" and
/// having every order go to the real account). Separation by type is
/// stronger than separation by string.
/// </summary>
public sealed class Trading212DemoOptions
{
    public const string SectionName = "Trading212Demo";

    public string ApiKey { get; set; } = string.Empty;

    /// <summary>Older T212 accounts use the key+secret pair with
    /// HTTP Basic auth. Newer accounts return only a single key and
    /// this stays empty. The client picks Basic vs raw based on
    /// whether this is set.</summary>
    public string ApiSecret { get; set; } = string.Empty;

    public int TimeoutSeconds { get; set; } = 15;

    public bool IsEnabled => !string.IsNullOrWhiteSpace(ApiKey);

    /// <summary>Hard-coded demo URL. Not configurable on purpose —
    /// the whole point of a separate demo client is to guarantee
    /// orders cannot reach live.trading212.com from this code path.</summary>
    public string BaseUrl => "https://demo.trading212.com/api/v0/";
}
