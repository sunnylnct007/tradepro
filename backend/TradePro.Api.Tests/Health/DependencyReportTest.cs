using System;
using System.Linq;
using TradePro.Api.Health;
using Xunit;

namespace TradePro.Api.Tests.Health;

/// <summary>
/// The verdict behind /health.
///
/// What these defend: on 16 Sep 2026 /health returned a hardcoded `status:"ok"`
/// all afternoon while the IBKR integration was entirely disabled by an IAM
/// drift. The loader HAD logged the denial three times. Logging is not
/// shouting. These tests are about the verdict being both truthful and safe to
/// serve.
/// </summary>
public class DependencyReportTest
{
    [Fact]
    public void A_fresh_report_is_ok_not_degraded()
    {
        // A preflight that has not run yet must NOT invent a fault. A false
        // alarm trains people to ignore this field, which is exactly how the
        // real signal got lost last time.
        var r = new DependencyReport();
        Assert.Equal("ok", r.Status);
        Assert.Empty(r.Failing);
        Assert.Null(r.LastCheckedUtc);
    }

    [Fact]
    public void One_failing_critical_dependency_degrades_the_whole_verdict()
    {
        var r = new DependencyReport();
        r.Record("database", true, "reachable");
        r.Record("secret:tradepro/ibkr", false, "not authorized to perform: secretsmanager:GetSecretValue");

        Assert.Equal("degraded", r.Status);
        var f = Assert.Single(r.Failing);
        Assert.Equal("secret:tradepro/ibkr", f.Name);
        // The CAUSE must survive into the payload — "degraded" alone sends
        // someone back into docker logs, which is where this hid for months.
        Assert.Contains("not authorized", f.Detail, StringComparison.Ordinal);
    }

    [Fact]
    public void A_non_critical_failure_is_reported_but_does_not_degrade()
    {
        // Context without false alarms: a secret that is genuinely optional
        // must not cry wolf, or the degraded flag stops meaning anything.
        var r = new DependencyReport();
        r.Record("secret:tradepro/ig", false, "absent", critical: false);

        Assert.Equal("ok", r.Status);
        Assert.Empty(r.Failing);
        Assert.Single(r.All);          // still visible for diagnosis
    }

    [Fact]
    public void Re_recording_a_dependency_replaces_it_rather_than_duplicating()
    {
        // A later pass that finds the dependency recovered must clear the
        // fault, not leave a stale failure pinned next to a fresh success.
        var r = new DependencyReport();
        r.Record("ibkr:config", false, "disabled");
        Assert.Equal("degraded", r.Status);

        r.Record("ibkr:config", true, "enabled, mode=paper");
        Assert.Equal("ok", r.Status);
        Assert.Single(r.All);
    }

    [Fact]
    public void Checks_are_ordered_stably_so_the_payload_does_not_churn()
    {
        var r = new DependencyReport();
        r.Record("secret:tradepro/ibkr", true, "loaded");
        r.Record("database", true, "reachable");
        r.Record("ibkr:config", true, "enabled");

        Assert.Equal(
            new[] { "database", "ibkr:config", "secret:tradepro/ibkr" },
            r.All.Select(c => c.Name).ToArray());
    }

    [Fact]
    public void MarkChecked_records_when_the_preflight_actually_ran()
    {
        // Without this a stale "ok" is indistinguishable from a fresh one.
        var r = new DependencyReport();
        var t = new DateTime(2026, 9, 16, 16, 30, 0, DateTimeKind.Utc);
        r.MarkChecked(t);
        Assert.Equal(t, r.LastCheckedUtc);
    }
}
