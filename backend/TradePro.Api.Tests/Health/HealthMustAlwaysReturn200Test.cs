using System;
using System.IO;
using System.Linq;
using Xunit;

namespace TradePro.Api.Tests.Health;

/// <summary>
/// /health must answer 200 even when a dependency is broken.
///
/// This is not a style rule, it is an outage guard. /health is the Docker
/// HEALTHCHECK for BOTH containers:
///
///   api:       test: ["CMD", "curl", "-fsS", "http://localhost:5080/health"]
///   frontend:  test: ["CMD-SHELL", "wget -q -O - http://localhost/health ..."]
///
/// `curl -fsS` and `wget` both fail on any non-2xx. A degraded verdict answering
/// 503 would mark the api container unhealthy, restart-loop it, and take the
/// frontend with it — the preflight causing the very outage it exists to
/// reveal. Precedent: on 13 Sep 2026 one unpullable image took the entire site
/// down because `up --force-recreate` tears the stack down before it pulls.
///
/// The verdict belongs in the BODY (`status: "degraded"` plus the named
/// failing dependency), never in the status code.
/// </summary>
public class HealthMustAlwaysReturn200Test
{
    private static string HealthSource()
    {
        var dir = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && dir is not null; i++)
        {
            var c = Path.Combine(dir, "TradePro.Api", "Endpoints", "HealthEndpoints.cs");
            if (File.Exists(c)) return File.ReadAllText(c);
            dir = Path.GetDirectoryName(dir);
        }
        throw new FileNotFoundException("HealthEndpoints.cs not found from the test output dir.");
    }

    [Fact]
    public void The_health_route_never_returns_a_failure_status_code()
    {
        var src = HealthSource();
        var start = src.IndexOf("MapGet(\"/health\"", StringComparison.Ordinal);
        Assert.True(start > 0, "the /health route could not be located");

        // The route body, up to the next route registration.
        var next = src.IndexOf("app.MapGet(", start + 10, StringComparison.Ordinal);
        var body = next > start ? src[start..next] : src[start..];

        string[] forbidden =
        {
            "StatusCodes.Status503",
            "StatusCodes.Status500",
            "Results.StatusCode",
            "Results.Problem",
            "Results.Json(", // a Json( ..., statusCode: ) overload can set one
        };
        var hits = forbidden.Where(f => body.Contains(f, StringComparison.Ordinal)).ToList();

        Assert.True(hits.Count == 0,
            "/health may return ONLY 200 — it is the Docker healthcheck for the api AND "
            + "frontend containers (curl -fsS / wget, both fail on non-2xx). Returning "
            + string.Join(", ", hits)
            + " would restart-loop the API and take the site down. Put the verdict in the "
            + "response BODY (status: \"degraded\" + the failing dependency) instead.");
    }

    [Fact]
    public void The_health_route_actually_reports_the_dependency_verdict()
    {
        // Guards the opposite regression: someone "simplifies" the endpoint back
        // to a hardcoded ok, and it silently stops being able to fail again.
        var src = HealthSource();
        // Scope to the /health ROUTE BODY. This file also serves a richer
        // status endpoint that legitimately carries its own nested
        // `api.status = "ok"`; asserting across the whole file would fail on
        // unrelated code and teach the next person to delete this test.
        var start = src.IndexOf("MapGet(\"/health\"", StringComparison.Ordinal);
        Assert.True(start > 0, "the /health route could not be located");
        var next = src.IndexOf("app.MapGet(", start + 10, StringComparison.Ordinal);
        var body = next > start ? src[start..next] : src[start..];

        Assert.Contains("DependencyReport", body, StringComparison.Ordinal);
        Assert.Contains("deps.Status", body, StringComparison.Ordinal);
        Assert.Contains("failing", body, StringComparison.Ordinal);
        Assert.DoesNotContain("status = \"ok\"", body, StringComparison.Ordinal);
    }
}

/// <summary>
/// The preflight must never be able to stop the app from starting.
/// </summary>
public class PreflightMustNotAbortStartupTest
{
    private static string PreflightSource()
    {
        var dir = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && dir is not null; i++)
        {
            var c = Path.Combine(dir, "TradePro.Api", "Health", "DependencyPreflight.cs");
            if (File.Exists(c)) return File.ReadAllText(c);
            dir = Path.GetDirectoryName(dir);
        }
        throw new FileNotFoundException("DependencyPreflight.cs not found.");
    }

    [Fact]
    public void StartAsync_wraps_every_check_so_it_cannot_abort_the_host()
    {
        // A hosted service throwing from StartAsync ABORTS HOST STARTUP. The
        // container would never come up and Docker would restart-loop it — a
        // health check causing a total outage. Same shape as 13 Sep 2026, when
        // one unpullable image took the entire site down.
        var src = PreflightSource();
        var start = src.IndexOf("public async Task StartAsync", StringComparison.Ordinal);
        Assert.True(start > 0, "StartAsync not found");
        var end = src.IndexOf("public Task StopAsync", start, StringComparison.Ordinal);
        var body = end > start ? src[start..end] : src[start..];

        Assert.Contains("try", body, StringComparison.Ordinal);
        Assert.Contains("catch (Exception", body, StringComparison.Ordinal);
    }
}
