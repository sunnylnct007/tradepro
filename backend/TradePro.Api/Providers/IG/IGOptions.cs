namespace TradePro.Api.Providers.IG;

/// <summary>
/// IG broker configuration. Values come from AWS Secrets Manager
/// under <c>tradepro/ig</c> (api_key / username / password / mode /
/// account_id) — kept out of code + out of git per the secrets pattern
/// the rest of the providers follow.
///
/// Bound from Configuration in Program.cs (which merges
/// <see cref="Auth.SecretsBundleLoader"/> so the IG secret values
/// flow in via the same chain as T212 + Firebase).
///
/// IG's REST gateway sits at:
///   demo: https://demo-api.ig.com/gateway/deal
///   live: https://api.ig.com/gateway/deal
///
/// Session model: POST /session with username + password + api-key
/// returns CST + X-SECURITY-TOKEN headers. Both must be sent on every
/// subsequent call; tokens expire after ~6 hours. See <see cref="IGClient"/>
/// for the re-login dance.
/// </summary>
public sealed class IGOptions
{
    /// <summary>"demo" / "live" / "disabled". Disabled = no IG calls
    /// happen; client is constructed but every method is a no-op so
    /// the rest of the app still compiles + boots without an IG creds
    /// configured.</summary>
    public string Mode { get; set; } = "disabled";

    public string ApiKey { get; set; } = string.Empty;
    public string Username { get; set; } = string.Empty;
    public string Password { get; set; } = string.Empty;

    /// <summary>Optional — only when the user has multiple accounts
    /// under the same login (rare for demo).</summary>
    public string? AccountId { get; set; }

    public bool IsEnabled =>
        !string.Equals(Mode, "disabled", StringComparison.OrdinalIgnoreCase)
        && !string.IsNullOrWhiteSpace(ApiKey)
        && !string.IsNullOrWhiteSpace(Username)
        && !string.IsNullOrWhiteSpace(Password);

    public string BaseUrl => Mode.ToLowerInvariant() switch
    {
        "live" => "https://api.ig.com/gateway/deal/",
        _      => "https://demo-api.ig.com/gateway/deal/",
    };

    /// <summary>Broker label stamped into oms_orders.broker so the
    /// OMS event log shows whether a fill came from IG demo vs live.</summary>
    public string BrokerLabel =>
        string.Equals(Mode, "live", StringComparison.OrdinalIgnoreCase)
            ? "IG_LIVE" : "IG_DEMO";
}
