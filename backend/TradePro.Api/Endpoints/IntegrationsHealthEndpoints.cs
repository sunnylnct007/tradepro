using System.Net.Http.Json;
using System.Web;
using Microsoft.Extensions.Options;
using TradePro.Api.Providers.Finnhub;
using TradePro.Api.Providers.Trading212;
using TradePro.Api.Simulation;

namespace TradePro.Api.Endpoints;

/// <summary>
/// Liveness of every external dependency, surfaced as a single endpoint
/// the Health page can render. Without this the user can't tell whether
/// today's verdicts came from healthy data — a silently 401'd Finnhub
/// or a yfinance rate-limit produces an empty cell that masquerades as
/// "no signal".
///
/// Probe taxonomy:
///   * T212    — live cheap call to /equity/account/summary via the
///               status probe we already wrote.
///   * Finnhub — live cheap call to /quote?symbol=AAPL (1/60 of the
///               free-tier per-minute quota; we cache the result for
///               30s so a tab refresh doesn't spam).
///   * Yahoo   — derived from the compare cache. If the most recent
///               payload's `errors[]` is empty → ok. If any row has
///               an error → degraded. The .NET api never calls Yahoo
///               directly so a live probe isn't worth its complexity.
///   * Ollama  — derived from the compare cache's llm.healthy field
///               on the most recent payload. Same rationale as Yahoo.
/// </summary>
public static class IntegrationsHealthEndpoints
{
    private static readonly object _cacheLock = new();
    private static (DateTime At, string Status, string Detail, int? Latency)? _finnhubCached;
    private static (DateTime At, string Status, string Detail, int? Latency)? _t212Cached;
    private static readonly TimeSpan _cacheTtl = TimeSpan.FromSeconds(30);

    public static IEndpointRouteBuilder MapIntegrationsHealthEndpoints(
        this IEndpointRouteBuilder app)
    {
        app.MapGet("/health/integrations",
            async (
                Trading212Client t212,
                FinnhubClient finnhub,
                ICompareStore compareStore,
                CancellationToken ct) =>
            {
                var t212Probe = await ProbeT212(t212, ct);
                var finnhubProbe = await ProbeFinnhub(finnhub, ct);
                var (yahooProbe, ollamaProbe) = ProbeFromCache(compareStore);

                var providers = new[] { t212Probe, finnhubProbe, yahooProbe, ollamaProbe };
                var verdict =
                    providers.Any(p => p.Status == "down") ? "needs_attention"
                    : providers.Any(p => p.Status == "degraded") ? "warn"
                    : "ok";

                return Results.Ok(new
                {
                    verdict,
                    utc = DateTime.UtcNow,
                    providers,
                });
            });

        return app;
    }

    private static async Task<ProviderHealth> ProbeT212(
        Trading212Client client, CancellationToken ct)
    {
        if (!client.IsEnabled)
        {
            return new ProviderHealth(
                Provider: "trading212",
                Label: "Trading 212",
                Status: "disabled",
                Detail: "Set TRADEPRO_T212_MODE + TRADEPRO_T212_API_KEY in .env to enable.",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: client.Mode);
        }

        // Cached probe — T212's docs cap /equity/account/summary at
        // 1 req / 1s and we don't want a noisy Health page to crowd
        // out the actual portfolio fetch.
        lock (_cacheLock)
        {
            if (_t212Cached is { } c && DateTime.UtcNow - c.At < _cacheTtl)
            {
                return new ProviderHealth(
                    Provider: "trading212",
                    Label: "Trading 212",
                    Status: c.Status,
                    Detail: c.Detail + " (cached)",
                    LatencyMs: c.Latency,
                    LastCheckedUtc: c.At,
                    Mode: client.Mode);
            }
        }

        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var status = await client.GetStatusAsync(ct);
            sw.Stop();
            var rolledStatus =
                !status.Reachable ? "down"
                : !status.Authenticated ? "degraded"
                : "ok";
            var detail = status.Detail ?? "";
            var ms = (int)sw.ElapsedMilliseconds;
            lock (_cacheLock) { _t212Cached = (DateTime.UtcNow, rolledStatus, detail, ms); }
            return new ProviderHealth(
                Provider: "trading212",
                Label: "Trading 212",
                Status: rolledStatus,
                Detail: detail,
                LatencyMs: ms,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: status.Mode);
        }
        catch (Exception ex)
        {
            sw.Stop();
            var detail = $"probe failed: {ex.Message}";
            lock (_cacheLock) { _t212Cached = (DateTime.UtcNow, "down", detail, null); }
            return new ProviderHealth(
                Provider: "trading212",
                Label: "Trading 212",
                Status: "down",
                Detail: detail,
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: client.Mode);
        }
    }

    private static async Task<ProviderHealth> ProbeFinnhub(
        FinnhubClient client, CancellationToken ct)
    {
        if (!client.IsEnabled)
        {
            return new ProviderHealth(
                Provider: "finnhub",
                Label: "Finnhub (earnings calendar)",
                Status: "disabled",
                Detail: "Set TRADEPRO_FINNHUB_API_KEY in .env to enable forward-earnings warnings.",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: null);
        }

        lock (_cacheLock)
        {
            if (_finnhubCached is { } c && DateTime.UtcNow - c.At < _cacheTtl)
            {
                return new ProviderHealth(
                    Provider: "finnhub",
                    Label: "Finnhub (earnings calendar)",
                    Status: c.Status,
                    Detail: c.Detail + " (cached)",
                    LatencyMs: c.Latency,
                    LastCheckedUtc: c.At,
                    Mode: null);
            }
        }

        // Use the dedicated /quote probe — checks auth + reachability
        // without depending on AAPL having upcoming earnings (which it
        // legitimately won't in the gap between Q1 and Q2 reporting).
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var statusCode = await client.ProbeAsync(ct);
            sw.Stop();
            var ms = (int)sw.ElapsedMilliseconds;
            string status, detail;
            if (statusCode is null)
            {
                status = "down";
                detail = "probe failed (network or timeout)";
            }
            else if (statusCode == 200)
            {
                status = "ok";
                detail = $"quote probe: HTTP 200 in {ms}ms";
            }
            else if (statusCode == 401 || statusCode == 403)
            {
                status = "down";
                detail = $"auth rejected (HTTP {statusCode}) — check TRADEPRO_FINNHUB_API_KEY";
            }
            else
            {
                status = "degraded";
                detail = $"unexpected HTTP {statusCode}";
            }
            lock (_cacheLock) { _finnhubCached = (DateTime.UtcNow, status, detail, ms); }
            return new ProviderHealth(
                Provider: "finnhub",
                Label: "Finnhub (earnings calendar)",
                Status: status,
                Detail: detail,
                LatencyMs: ms,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: null);
        }
        catch (Exception ex)
        {
            sw.Stop();
            var detail = $"probe failed: {ex.Message}";
            lock (_cacheLock) { _finnhubCached = (DateTime.UtcNow, "down", detail, null); }
            return new ProviderHealth(
                Provider: "finnhub",
                Label: "Finnhub (earnings calendar)",
                Status: "down",
                Detail: detail,
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: null);
        }
    }

    /// <summary>Yahoo + Ollama don't have a .NET-side client, so we
    /// derive their state from the most-recent compare payload's
    /// `errors[]` and `llm.healthy` fields. Best-effort: silent on
    /// any cache read failure.</summary>
    private static (ProviderHealth Yahoo, ProviderHealth Ollama) ProbeFromCache(
        ICompareStore store)
    {
        var summaries = store.ListUniverses();
        if (summaries.Count == 0)
        {
            var noCache = "no compare cache yet — run a refresh to populate";
            return (
                new ProviderHealth(
                    Provider: "yahoo",
                    Label: "Yahoo Finance (prices + fundamentals)",
                    Status: "degraded",
                    Detail: noCache,
                    LatencyMs: null,
                    LastCheckedUtc: DateTime.UtcNow,
                    Mode: null),
                new ProviderHealth(
                    Provider: "ollama",
                    Label: "Ollama (local LLM)",
                    Status: "degraded",
                    Detail: noCache,
                    LatencyMs: null,
                    LastCheckedUtc: DateTime.UtcNow,
                    Mode: null));
        }

        // Most recently generated payload across all universes wins.
        var newest = summaries.OrderByDescending(s => s.GeneratedAtUtc).First();
        var ageHours = (DateTime.UtcNow - newest.GeneratedAtUtc).TotalHours;

        var freshness =
            ageHours < 6 ? "fresh"
            : ageHours < 24 ? "ok-but-aging"
            : ageHours < 72 ? "stale"
            : "very-stale";

        // Yahoo: cache age is the only signal we have — if it's recent,
        // Yahoo was happy when the worker last ran. Anything older
        // than 24h flags as degraded so the user knows verdicts are
        // not from today's prices.
        var yahooStatus = ageHours < 24 ? "ok" : (ageHours < 72 ? "degraded" : "down");
        var yahoo = new ProviderHealth(
            Provider: "yahoo",
            Label: "Yahoo Finance (prices + fundamentals)",
            Status: yahooStatus,
            Detail: $"newest cache: {newest.Universe} {(int)ageHours}h ago ({freshness})",
            LatencyMs: null,
            LastCheckedUtc: newest.GeneratedAtUtc,
            Mode: null);

        // Ollama: same age signal — if the cache is fresh, the LLM
        // ran successfully (sentiment scoring is a hard requirement
        // for the comparator). When >24h we mark degraded.
        var ollama = new ProviderHealth(
            Provider: "ollama",
            Label: "Ollama (local LLM, sentiment + rationale)",
            Status: yahooStatus,  // same logic — both ride the same heartbeat
            Detail: $"last successful run: {(int)ageHours}h ago",
            LatencyMs: null,
            LastCheckedUtc: newest.GeneratedAtUtc,
            Mode: null);

        return (yahoo, ollama);
    }
}

/// <summary>One provider's row on the integrations health panel.</summary>
public sealed record ProviderHealth(
    string Provider,           // stable id, used for sort + iconography
    string Label,              // human-friendly name
    string Status,             // ok | degraded | down | disabled
    string Detail,             // one-line explanation
    int? LatencyMs,            // last probe round-trip when applicable
    DateTime LastCheckedUtc,
    string? Mode);             // T212-only: demo / live / disabled
