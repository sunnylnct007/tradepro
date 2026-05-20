using Amazon;
using Amazon.SecretsManager;
using Amazon.SecretsManager.Model;
using System.Text.Json;

namespace TradePro.Api.Auth;

/// <summary>
/// Boot-time loader that pulls the shared `tradepro/all` JSON bundle
/// from AWS Secrets Manager and folds the keys into IConfiguration so
/// the rest of the app sees them as if they were env vars.
///
/// Key mapping (kebab-case in SM → colon-form in config):
///     t212-api-key      → Trading212:ApiKey
///     t212-api-secret   → Trading212:ApiSecret
///     t212-mode         → Trading212:Mode
///     finnhub-api-key   → Finnhub:ApiKey
///     ingest-token      → Ingest:Token
///
/// Anything else in the bundle is ignored — extra keys are fine, they
/// just don't show up in IConfiguration. Existing values already in
/// IConfiguration (env vars, appsettings.json) override the SM bundle,
/// so local dev keeps working without AWS creds.
///
/// Failure mode: logs + continues. If SM is unreachable, the app boots
/// with whatever values are already in IConfiguration. The /health
/// endpoint is the right place to expose "loaded N keys from SM" later.
/// </summary>
public static class SecretsBundleLoader
{
    private const string DefaultSecretName = "tradepro/all";
    private const string DefaultRegion = "eu-north-1";

    private static readonly Dictionary<string, string> KeyMap = new()
    {
        // Trading 212 LIVE — reads (positions, status, instruments).
        // The "t212-api-key" / "t212-api-secret" pair stays as the live
        // credentials for backward compat with deployments that pre-date
        // the dual-mode split. Renaming to "t212-live-*" would force every
        // existing SM bundle to rotate which we don't need.
        ["t212-api-key"]      = "Trading212:ApiKey",
        ["t212-api-secret"]   = "Trading212:ApiSecret",
        ["t212-mode"]         = "Trading212:Mode",
        // Trading 212 DEMO — writes (order placement) + demo positions.
        // Bound to Trading212Demo:* by Trading212DemoOptions. The base URL
        // is hard-coded to demo.trading212.com in the demo client so no
        // "demo-mode" key is needed here.
        ["t212-demo-api-key"]    = "Trading212Demo:ApiKey",
        ["t212-demo-api-secret"] = "Trading212Demo:ApiSecret",
        ["finnhub-api-key"] = "Finnhub:ApiKey",
        ["ingest-token"]    = "Ingest:Token",
    };

    public static void LoadInto(IConfigurationBuilder builder, IConfiguration existing, ILogger? log = null)
    {
        var secretName = existing["Secrets:BundleName"] ?? DefaultSecretName;
        var region = existing["Secrets:BundleRegion"] ?? DefaultRegion;
        var disabled = string.Equals(
            existing["Secrets:BundleDisabled"], "true", StringComparison.OrdinalIgnoreCase);
        if (disabled)
        {
            log?.LogInformation("SM bundle disabled via Secrets:BundleDisabled");
            return;
        }

        Dictionary<string, string?>? bundle;
        try
        {
            bundle = FetchBundle(secretName, region);
        }
        catch (Exception ex)
        {
            log?.LogWarning(ex, "SM bundle fetch failed (name={name}, region={region}); continuing with env/appsettings values only", secretName, region);
            return;
        }
        if (bundle is null || bundle.Count == 0)
        {
            log?.LogInformation("SM bundle empty or absent (name={name}, region={region})", secretName, region);
            return;
        }

        // Only inject keys that don't already have a value in
        // IConfiguration — env vars and appsettings.json win, so local
        // overrides keep working.
        var injected = new Dictionary<string, string?>();
        foreach (var (kebab, configKey) in KeyMap)
        {
            if (!bundle.TryGetValue(kebab, out var value) || string.IsNullOrEmpty(value)) continue;
            var current = existing[configKey];
            if (!string.IsNullOrEmpty(current))
            {
                log?.LogDebug("SM bundle key {kebab} skipped: {configKey} already set", kebab, configKey);
                continue;
            }
            injected[configKey] = value;
        }

        if (injected.Count > 0)
        {
            builder.AddInMemoryCollection(injected);
            log?.LogInformation("SM bundle loaded {count} key(s) from {name}: {keys}",
                injected.Count, secretName, string.Join(", ", injected.Keys));
        }
    }

    private static Dictionary<string, string?>? FetchBundle(string secretName, string region)
    {
        var client = new AmazonSecretsManagerClient(RegionEndpoint.GetBySystemName(region));
        var resp = client.GetSecretValueAsync(new GetSecretValueRequest { SecretId = secretName })
            .GetAwaiter().GetResult();
        if (string.IsNullOrEmpty(resp.SecretString)) return null;
        var parsed = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(resp.SecretString);
        if (parsed is null) return null;
        var result = new Dictionary<string, string?>();
        foreach (var (k, v) in parsed)
        {
            result[k] = v.ValueKind == JsonValueKind.String ? v.GetString() : v.ToString();
        }
        return result;
    }
}
