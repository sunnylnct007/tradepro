using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using Microsoft.Extensions.Options;

namespace TradePro.Api.Providers.Trading212;

/// <summary>
/// Typed HttpClient for the Trading 212 Public API.
///
/// Only the read-only metadata + portfolio surface is wired up so far.
/// Order placement is intentionally off until we have a one-button UI
/// safety story — the API will happily place a real-money trade with
/// the wrong key.
/// </summary>
public sealed class Trading212Client
{
    private readonly HttpClient _http;
    private readonly Trading212Options _options;
    private readonly ILogger<Trading212Client> _log;

    public Trading212Client(
        HttpClient http,
        IOptions<Trading212Options> options,
        ILogger<Trading212Client> log)
    {
        _http = http;
        _options = options.Value;
        _log = log;
        if (_options.IsEnabled)
        {
            _http.BaseAddress = new Uri(_options.BaseUrl);
            var token = Convert.ToBase64String(
                Encoding.UTF8.GetBytes($"{_options.ApiKey}:{_options.ApiSecret}"));
            _http.DefaultRequestHeaders.Authorization =
                new AuthenticationHeaderValue("Basic", token);
            _http.Timeout = TimeSpan.FromSeconds(_options.TimeoutSeconds);
        }
    }

    public bool IsEnabled => _options.IsEnabled;
    public string Mode => _options.Mode;

    /// <summary>Hits <c>/equity/account/cash</c> — smallest authenticated
    /// call we can make to prove credentials and connectivity.</summary>
    public async Task<Trading212Status> GetStatusAsync(CancellationToken ct)
    {
        if (!_options.IsEnabled)
        {
            return new Trading212Status(
                Configured: false,
                Mode: _options.Mode,
                Reachable: false,
                Authenticated: false,
                Detail: "Trading212 integration disabled or missing credentials.");
        }

        try
        {
            using var resp = await _http.GetAsync("equity/account/cash", ct);
            var rateLimitRemaining = TryHeaderInt(resp, "x-ratelimit-remaining");
            if (resp.StatusCode == HttpStatusCode.Unauthorized)
            {
                return new Trading212Status(
                    Configured: true,
                    Mode: _options.Mode,
                    Reachable: true,
                    Authenticated: false,
                    Detail: "401 Unauthorized — check API key/secret pair.",
                    RateLimitRemaining: rateLimitRemaining);
            }
            resp.EnsureSuccessStatusCode();
            return new Trading212Status(
                Configured: true,
                Mode: _options.Mode,
                Reachable: true,
                Authenticated: true,
                Detail: $"OK (HTTP {(int)resp.StatusCode})",
                RateLimitRemaining: rateLimitRemaining);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Trading212 status probe failed");
            return new Trading212Status(
                Configured: true,
                Mode: _options.Mode,
                Reachable: false,
                Authenticated: false,
                Detail: ex.Message);
        }
    }

    private static int? TryHeaderInt(HttpResponseMessage resp, string name)
    {
        if (resp.Headers.TryGetValues(name, out var vals)
            && int.TryParse(vals.FirstOrDefault(), out var n))
        {
            return n;
        }
        return null;
    }
}

public sealed record Trading212Status(
    bool Configured,
    string Mode,
    bool Reachable,
    bool Authenticated,
    string Detail,
    int? RateLimitRemaining = null);
