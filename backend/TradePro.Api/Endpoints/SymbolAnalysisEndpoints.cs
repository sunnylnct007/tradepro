using System.Net.Http;
using System.Text;
using System.Web;

namespace TradePro.Api.Endpoints;

/// Proxy for the Python Symbol Analysis sidecar (tradepro-analysis-server).
/// Same pattern as DocumentEndpoints.MapPost("/upload") forwarding to the
/// extractor sidecar — the .NET API does auth + rate-limit, the Python
/// side does the actual orchestration (build_symbol_analysis_card).
///
/// Why proxy: the frontend must call through /api/* so Firebase auth is
/// enforced; the sidecar itself is open (CORS-loose for local dev).
/// Production deployments keep the sidecar reachable only from the .NET
/// API container — no public exposure.
///
/// Configuration: `Analysis:Url` (default http://analysis:8002) names
/// the sidecar base URL inside the cluster. Docker-compose service name
/// "analysis" maps to that container.
public static class SymbolAnalysisEndpoints
{
    public static IEndpointRouteBuilder MapSymbolAnalysisEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/symbol-analysis").WithTags("SymbolAnalysis");

        group.MapGet("/{ticker}",
            async (string ticker,
                   string? universe,
                   double? drawdownPct,
                   bool? skipLongTerm,
                   IConfiguration config,
                   IHttpClientFactory clientFactory,
                   ILoggerFactory logFactory) =>
            {
                if (string.IsNullOrWhiteSpace(ticker))
                {
                    return Results.BadRequest(new { error = "ticker is required" });
                }

                var sidecarBase = (config["Analysis:Url"] ?? "http://analysis:8002")
                    .TrimEnd('/');
                var log = logFactory.CreateLogger("SymbolAnalysisEndpoints");

                // Build query string forwarded to the sidecar — keep
                // identical parameter names so behaviour matches the
                // Python side exactly.
                var q = new StringBuilder();
                void Append(string key, string value)
                {
                    if (q.Length > 0) q.Append('&');
                    q.Append(key).Append('=').Append(HttpUtility.UrlEncode(value));
                }
                if (!string.IsNullOrWhiteSpace(universe))
                    Append("universe", universe);
                if (drawdownPct.HasValue)
                    Append("drawdown_pct", drawdownPct.Value.ToString(
                        System.Globalization.CultureInfo.InvariantCulture));
                if (skipLongTerm.HasValue)
                    Append("skip_long_term", skipLongTerm.Value ? "true" : "false");

                var encodedTicker = HttpUtility.UrlEncode(ticker.Trim().ToUpperInvariant());
                var url = $"{sidecarBase}/symbol/{encodedTicker}/analysis"
                    + (q.Length > 0 ? "?" + q : string.Empty);

                using var client = clientFactory.CreateClient();
                client.Timeout = TimeSpan.FromSeconds(45);  // generous: yfinance can be slow

                HttpResponseMessage sidecarResp;
                try
                {
                    sidecarResp = await client.GetAsync(url);
                }
                catch (Exception ex)
                {
                    log.LogError(ex, "analysis sidecar unreachable at {Url}", sidecarBase);
                    return Results.Problem(
                        $"Symbol Analysis sidecar is not running. Start it with " +
                        $"`docker compose --profile analysis up -d analysis` " +
                        $"(or locally `uv run tradepro-analysis-server`). ({ex.Message})",
                        statusCode: 502);
                }

                var body = await sidecarResp.Content.ReadAsStringAsync();
                if (!sidecarResp.IsSuccessStatusCode)
                {
                    return Results.Problem(
                        $"analysis sidecar returned {(int)sidecarResp.StatusCode}: " +
                        body[..Math.Min(body.Length, 500)],
                        statusCode: (int)sidecarResp.StatusCode);
                }

                // Pass JSON through as-is — the sidecar's envelope is
                // already the contract the frontend expects.
                return Results.Content(body, "application/json");
            });

        return app;
    }
}
