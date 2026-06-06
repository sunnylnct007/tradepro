using System.Text.RegularExpressions;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Resolves the source IP that IBKR's <c>POST /gw/api/v1/sso-sessions</c>
/// requires as its <c>ip</c> claim — the egress IP of the host making the
/// Web API requests.
///
/// WHY AUTO-DETECT: hardcoding the IP (via <c>IBKR:SourceIp</c> / the secret's
/// <c>ip</c>) breaks every time the EC2 host restarts and gets a new public
/// IP. So the operator can OMIT <c>ip</c> from the secret and we detect the
/// backend's public egress IP at bring-up instead, falling back to the config
/// value ONLY when it's explicitly set (override).
///
/// RESOLUTION ORDER (see <see cref="PickIp"/> for the pure, testable rule):
///   1. <c>IBKR:SourceIp</c> non-empty → use it verbatim (explicit override).
///   2. else → HTTPS GET a public echo service (ipify, then AWS checkip)
///      with a short timeout, validate the body is a plausible IPv4.
///   3. all providers fail + no override → return null; the caller fails the
///      bring-up with a clear "set IBKR:SourceIp to override" error.
///
/// Detection is cached on <see cref="IBKRSessionCache"/> (it only changes on
/// restart / network change), so we detect once per process alongside the
/// session — not per request. A session <c>Clear()</c> (re-auth after a
/// failure) also drops the cached IP so it's re-detected.
///
/// Uses a NAMED HttpClient from IHttpClientFactory (registered in Program.cs)
/// rather than <c>new HttpClient()</c> per call, per the project's DI pattern.
/// </summary>
public sealed class IBKREgressIpResolver
{
    /// <summary>Named-client key registered via AddHttpClient in Program.cs.</summary>
    public const string HttpClientName = "ibkr-egress-ip";

    // Plausible-IPv4 guard: four 0-255 octets. We deliberately do NOT accept
    // IPv6 / hostnames — IBKR's sso-sessions ip claim is the dotted-quad the
    // egress NAT presents, and an echo service that returned junk (an HTML
    // error page, an IPv6 address we can't use) must be rejected, not sent.
    private static readonly Regex Ipv4 = new(
        @"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$",
        RegexOptions.Compiled);

    // Public IP-echo endpoints, tried in order. ipify returns the raw IP as
    // plain text; AWS checkip returns the IP + a trailing newline (trimmed).
    private static readonly string[] EchoUrls =
    {
        "https://api.ipify.org",
        "https://checkip.amazonaws.com",
    };

    private static readonly TimeSpan ProviderTimeout = TimeSpan.FromSeconds(3);

    private readonly IHttpClientFactory _httpFactory;
    private readonly ILogger<IBKREgressIpResolver> _log;

    public IBKREgressIpResolver(
        IHttpClientFactory httpFactory, ILogger<IBKREgressIpResolver> log)
    {
        _httpFactory = httpFactory;
        _log = log;
    }

    /// <summary>True for a syntactically plausible dotted-quad IPv4.</summary>
    public static bool IsPlausibleIpv4(string? candidate) =>
        !string.IsNullOrWhiteSpace(candidate) && Ipv4.IsMatch(candidate.Trim());

    /// <summary>
    /// PURE selection rule (no I/O — unit-tested): given the configured
    /// override and an already-detected IP, decide which IP to send and where
    /// it came from. Override wins when present AND valid; otherwise the
    /// detected IP is used when valid; otherwise <c>(null, null)</c> meaning
    /// "no usable IP — fail the bring-up".
    /// </summary>
    public static (string? Ip, string? Source) PickIp(string? overrideIp, string? detectedIp)
    {
        if (IsPlausibleIpv4(overrideIp))
            return (overrideIp!.Trim(), "override");
        if (IsPlausibleIpv4(detectedIp))
            return (detectedIp!.Trim(), "auto-detected");
        return (null, null);
    }

    /// <summary>
    /// Resolve the IP to use for the sso-sessions claim, caching the detected
    /// value on the session. Returns (ip, source) where source is
    /// "override" | "auto-detected". Throws <see cref="InvalidOperationException"/>
    /// with a clear, operator-actionable message when no override is set AND
    /// detection fails — so we never send an empty/null ip to IBKR.
    /// </summary>
    public async Task<(string Ip, string Source)> ResolveAsync(
        string? overrideIp, IBKRSessionCache session, CancellationToken ct)
    {
        // 1. Explicit override always wins (current behaviour preserved).
        if (IsPlausibleIpv4(overrideIp))
            return (overrideIp!.Trim(), "override");

        // 2. Reuse a previously-detected IP for this process/session.
        if (IsPlausibleIpv4(session.DetectedEgressIp))
            return (session.DetectedEgressIp!, "auto-detected");

        // 3. Detect via the echo providers, then cache on the session.
        var detected = await DetectAsync(ct);
        if (detected is not null)
        {
            session.SetDetectedEgressIp(detected);
            return (detected, "auto-detected");
        }

        throw new InvalidOperationException(
            "could not determine egress IP for IBKR sso-sessions; "
            + "set IBKR:SourceIp to override");
    }

    /// <summary>Try each echo provider in order; return the first plausible
    /// IPv4, else null. Each attempt has a short independent timeout so a
    /// hung provider can't stall the bring-up.</summary>
    private async Task<string?> DetectAsync(CancellationToken ct)
    {
        var client = _httpFactory.CreateClient(HttpClientName);
        foreach (var url in EchoUrls)
        {
            try
            {
                using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
                cts.CancelAfter(ProviderTimeout);
                var body = (await client.GetStringAsync(url, cts.Token)).Trim();
                if (IsPlausibleIpv4(body))
                {
                    _log.LogInformation(
                        "IBKR egress IP auto-detected via {Url}", url);
                    return body;
                }
                _log.LogDebug(
                    "IBKR egress IP probe {Url} returned non-IPv4 body (len {Len})",
                    url, body.Length);
            }
            catch (Exception ex)
            {
                _log.LogDebug(ex, "IBKR egress IP probe {Url} failed", url);
            }
        }
        return null;
    }
}
