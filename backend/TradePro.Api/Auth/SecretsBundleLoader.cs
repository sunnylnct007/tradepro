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
    // eu-west-2 is where every TradePro secret + the EC2 host now live.
    // (Project originated in eu-north-1; the migration moved both
    // bundle and standalone secrets. Keep this in sync with the bundle.)
    private const string DefaultRegion = "eu-west-2";

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

    /// <summary>
    /// Map for the standalone <c>tradepro/ig</c> secret (snake_case
    /// inside the secret → IG:* config). Kept separate from KeyMap so
    /// the IG creds rotate independently of the all-in-one bundle.
    /// </summary>
    private static readonly Dictionary<string, string> IgKeyMap = new()
    {
        ["api_key"]    = "IG:ApiKey",
        ["username"]   = "IG:Username",
        ["password"]   = "IG:Password",
        ["mode"]       = "IG:Mode",
        ["account_id"] = "IG:AccountId",
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

        Dictionary<string, string?>? bundle = null;
        try
        {
            bundle = FetchBundle(secretName, region);
        }
        catch (Exception ex)
        {
            // Don't return — secondary secrets (tradepro/ig etc.) may
            // still load even when the primary bundle is unavailable.
            // Common case: operator dropped the legacy tradepro/all
            // secret but still wants per-broker secondaries to work.
            log?.LogWarning(ex, "SM bundle fetch failed (name={name}, region={region}); continuing with env/appsettings values only — will still attempt secondary secrets", secretName, region);
        }
        if (bundle is null || bundle.Count == 0)
        {
            log?.LogInformation("SM bundle empty or absent (name={name}, region={region}) — proceeding to secondary secrets", secretName, region);
        }
        else
        {
            // Only inject keys that don't already have a value in
            // IConfiguration — env vars and appsettings.json win, so
            // local overrides keep working.
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

        // Also fold in the standalone IG secret (tradepro/ig) when
        // present. Separate secret so IG creds rotate independently of
        // the all-in-one bundle. Loads unconditionally — primary bundle
        // failure must not gate the secondaries.
        LoadSecondary(builder, existing, region, "tradepro/ig", IgKeyMap, log);
    }

    private static void LoadSecondary(
        IConfigurationBuilder builder,
        IConfiguration existing,
        string region,
        string secretName,
        Dictionary<string, string> keyMap,
        ILogger? log)
    {
        Dictionary<string, string?>? bundle;
        try
        {
            bundle = FetchBundle(secretName, region);
        }
        catch (Exception ex)
        {
            // Elevated from Debug to Information — when the secret IS
            // expected (e.g. operator has populated tradepro/ig) and the
            // fetch fails silently, the operator stares at "enabled:false"
            // with no clue why. Log loud here so the failure mode is
            // visible in container output without rebuilding.
            log?.LogInformation(
                "Secondary secret {name} fetch failed: {msg} (region {region}). " +
                "If you expected this secret to load, check IAM permissions for the task role.",
                secretName, ex.Message, region);
            return;
        }
        if (bundle is null || bundle.Count == 0)
        {
            log?.LogInformation("Secondary secret {name} returned empty bundle", secretName);
            return;
        }
        var injected = new Dictionary<string, string?>();
        var skipped = new List<string>();
        foreach (var (key, configKey) in keyMap)
        {
            if (!bundle.TryGetValue(key, out var value) || string.IsNullOrEmpty(value))
            {
                skipped.Add($"{key}(missing)");
                continue;
            }
            if (!string.IsNullOrEmpty(existing[configKey]))
            {
                skipped.Add($"{key}(already-set)");
                continue;
            }
            injected[configKey] = value;
        }
        if (injected.Count > 0)
        {
            builder.AddInMemoryCollection(injected);
            log?.LogInformation("Secondary secret {name} loaded {count} key(s): {keys}",
                secretName, injected.Count, string.Join(", ", injected.Keys));
        }
        else
        {
            log?.LogInformation(
                "Secondary secret {name} found but injected 0 keys. skipped={skipped}",
                secretName, string.Join(", ", skipped));
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
