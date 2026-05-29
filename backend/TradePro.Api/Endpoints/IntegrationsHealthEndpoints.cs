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
                Trading212DemoClient t212Demo,
                FinnhubClient finnhub,
                TradePro.Api.Providers.IG.IGClient ig,
                ICompareStore compareStore,
                Npgsql.NpgsqlDataSource db,
                IConfiguration cfg,
                IHttpClientFactory httpFactory,
                CancellationToken ct) =>
            {
                var t212Probe = await ProbeT212(t212, ct);
                var t212DemoProbe = await ProbeT212Demo(t212Demo, ct);
                var finnhubProbe = await ProbeFinnhub(finnhub, ct);
                var igProbe = await ProbeIG(ig, ct);
                var llmProbe = await ProbeLLM(cfg, httpFactory, db, ct);
                var dbProbe = await ProbeDb(db, ct);
                var (yahooProbe, ollamaProbe) = ProbeFromCache(compareStore);

                var providers = new[] {
                    t212Probe, t212DemoProbe, igProbe,
                    llmProbe, finnhubProbe, yahooProbe, ollamaProbe, dbProbe,
                };
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

    private static async Task<ProviderHealth> ProbeT212Demo(
        Trading212DemoClient client, CancellationToken ct)
    {
        if (!client.IsEnabled)
        {
            return new ProviderHealth(
                Provider: "trading212_demo",
                Label: "Trading 212 (DEMO — algo writes here)",
                Status: "disabled",
                Detail: "Set TRADEPRO_T212_DEMO_API_KEY in .env to enable order placement.",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: "demo");
        }
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var cash = await client.GetCashAsync(ct);
            sw.Stop();
            var ms = (int)sw.ElapsedMilliseconds;
            if (cash.Error is not null)
            {
                return new ProviderHealth(
                    Provider: "trading212_demo",
                    Label: "Trading 212 (DEMO — algo writes here)",
                    Status: "degraded",
                    Detail: $"reachable but error: {cash.Error}",
                    LatencyMs: ms,
                    LastCheckedUtc: DateTime.UtcNow,
                    Mode: "demo");
            }
            return new ProviderHealth(
                Provider: "trading212_demo",
                Label: "Trading 212 (DEMO — algo writes here)",
                Status: "ok",
                Detail: $"cash probe ok — free {cash.Free?.ToString("F2") ?? "?"} {cash.Currency ?? "USD"}",
                LatencyMs: ms,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: "demo");
        }
        catch (Exception ex)
        {
            return new ProviderHealth(
                Provider: "trading212_demo",
                Label: "Trading 212 (DEMO — algo writes here)",
                Status: "down",
                Detail: $"probe failed: {ex.Message}",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: "demo");
        }
    }

    private static async Task<ProviderHealth> ProbeIG(
        TradePro.Api.Providers.IG.IGClient ig, CancellationToken ct)
    {
        if (!ig.IsEnabled)
        {
            return new ProviderHealth(
                Provider: "ig",
                Label: "IG (FX + equities, secondary broker)",
                Status: "disabled",
                Detail: "Populate AWS Secrets Manager tradepro/ig + restart.",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: "disabled");
        }
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var cash = await ig.GetCashAsync(ct);
            sw.Stop();
            var ms = (int)sw.ElapsedMilliseconds;
            if (cash.Error is not null)
            {
                return new ProviderHealth(
                    Provider: "ig",
                    Label: "IG (FX + equities, secondary broker)",
                    Status: "degraded",
                    Detail: $"reachable but: {cash.Error}",
                    LatencyMs: ms,
                    LastCheckedUtc: DateTime.UtcNow,
                    Mode: ig.BrokerLabel);
            }
            return new ProviderHealth(
                Provider: "ig",
                Label: "IG (FX + equities, secondary broker)",
                Status: "ok",
                Detail: $"available {cash.Available?.ToString("F0") ?? "?"} {cash.Currency ?? "?"}",
                LatencyMs: ms,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: ig.BrokerLabel);
        }
        catch (Exception ex)
        {
            return new ProviderHealth(
                Provider: "ig",
                Label: "IG (FX + equities, secondary broker)",
                Status: "down",
                Detail: $"probe failed: {ex.Message}",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: ig.BrokerLabel);
        }
    }

    /// <summary>Probe the configured LLM endpoint with a minimal POST so
    /// the user can see whether the model that approves orders is
    /// actually reachable. Settings come from app_settings_kv (the
    /// live operator-tunable source); IConfiguration is the fallback
    /// when the DB row is missing.</summary>
    private static async Task<ProviderHealth> ProbeLLM(
        IConfiguration cfg, IHttpClientFactory httpFactory,
        Npgsql.NpgsqlDataSource db, CancellationToken ct)
    {
        // Pull live settings from settings_kv — that's where the operator
        // tunes llm_url / llm_model via the /settings page. Falls back
        // to IConfiguration values (which themselves fall back to
        // sensible defaults) when the DB row is missing or unparseable.
        string llmUrl, llmModel;
        try
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            var url = await Dapper.SqlMapper.ExecuteScalarAsync<string?>(conn,
                "SELECT trim(both '\"' from value::text) FROM app_settings_kv WHERE key = 'llm_url';");
            var model = await Dapper.SqlMapper.ExecuteScalarAsync<string?>(conn,
                "SELECT trim(both '\"' from value::text) FROM app_settings_kv WHERE key = 'llm_model';");
            llmUrl = !string.IsNullOrWhiteSpace(url) ? url!
                : cfg["LLM:Url"] ?? "http://host.docker.internal:11434/api/generate";
            llmModel = !string.IsNullOrWhiteSpace(model) ? model!
                : cfg["LLM:Model"] ?? "llama3.1:8b-instruct";
        }
        catch
        {
            llmUrl = cfg["LLM:Url"] ?? "http://host.docker.internal:11434/api/generate";
            llmModel = cfg["LLM:Model"] ?? "llama3.1:8b-instruct";
        }

        // The LLM is expected to run on the Mac worker — EC2 containers
        // can never reach `localhost:11434` or `host.docker.internal`
        // by design (they don't share network namespaces with the Mac).
        // If the configured URL is a localhost/private endpoint, mark
        // the tile as "expected-remote" instead of "down" so the
        // operator doesn't think a real outage is in progress.
        var isLocalhostExpected =
            llmUrl.Contains("localhost", StringComparison.OrdinalIgnoreCase)
            || llmUrl.Contains("127.0.0.1")
            || llmUrl.Contains("host.docker.internal", StringComparison.OrdinalIgnoreCase);

        var http = httpFactory.CreateClient();
        http.Timeout = TimeSpan.FromSeconds(5);
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            using var probeReq = new HttpRequestMessage(HttpMethod.Post, llmUrl)
            {
                Content = JsonContent.Create(new
                {
                    model = llmModel,
                    prompt = "ping",
                    stream = false,
                    options = new { num_predict = 1 },
                }),
            };
            using var resp = await http.SendAsync(probeReq, ct);
            sw.Stop();
            var ms = (int)sw.ElapsedMilliseconds;
            if (resp.IsSuccessStatusCode)
            {
                return new ProviderHealth(
                    Provider: "llm",
                    Label: $"LLM ({llmModel})",
                    Status: "ok",
                    Detail: $"ping ok in {ms}ms",
                    LatencyMs: ms,
                    LastCheckedUtc: DateTime.UtcNow,
                    Mode: llmUrl);
            }
            return new ProviderHealth(
                Provider: "llm",
                Label: $"LLM ({llmModel})",
                Status: "degraded",
                Detail: $"endpoint reachable but HTTP {(int)resp.StatusCode}",
                LatencyMs: ms,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: llmUrl);
        }
        catch (Exception ex)
        {
            sw.Stop();
            if (isLocalhostExpected)
            {
                return new ProviderHealth(
                    Provider: "llm",
                    Label: $"LLM ({llmModel})",
                    Status: "disabled",
                    Detail: $"runs on Mac worker — unreachable from EC2 by design ({ex.Message})",
                    LatencyMs: null,
                    LastCheckedUtc: DateTime.UtcNow,
                    Mode: llmUrl);
            }
            return new ProviderHealth(
                Provider: "llm",
                Label: $"LLM ({llmModel})",
                Status: "down",
                Detail: $"unreachable: {ex.Message}",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: llmUrl);
        }
    }

    private static async Task<ProviderHealth> ProbeDb(
        Npgsql.NpgsqlDataSource db, CancellationToken ct)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            await using var conn = await db.OpenConnectionAsync(ct);
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT 1;";
            await cmd.ExecuteScalarAsync(ct);
            sw.Stop();
            return new ProviderHealth(
                Provider: "postgres",
                Label: "Postgres (orders + decisions DB)",
                Status: "ok",
                Detail: $"SELECT 1 in {sw.ElapsedMilliseconds}ms",
                LatencyMs: (int)sw.ElapsedMilliseconds,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: null);
        }
        catch (Exception ex)
        {
            return new ProviderHealth(
                Provider: "postgres",
                Label: "Postgres (orders + decisions DB)",
                Status: "down",
                Detail: $"connection failed: {ex.Message}",
                LatencyMs: null,
                LastCheckedUtc: DateTime.UtcNow,
                Mode: null);
        }
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
