using System;
using System.IO;
using System.Linq;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// SOURCE GUARD: every /iserver/marketdata/snapshot call must hold a line
/// lease.
///
/// This is a source test rather than a behavioural one on purpose. The failure
/// it prevents is invisible at runtime: an unleased snapshot call returns
/// perfectly good data and simply never gives its market-data lines back. The
/// bill arrives days later as a blank option chain on an unrelated job, which
/// is precisely how 16 Sep looked — six markets dark at 14:12:21Z, healthy 45
/// minutes later, nothing to find. No unit test of the calling endpoint would
/// have caught it. A grep will.
/// </summary>
public class IBKRSnapshotMustLeaseLinesTest
{
    private static string ClientSource()
    {
        var dir = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && dir is not null; i++)
        {
            var candidate = Path.Combine(
                dir, "TradePro.Api", "Providers", "IBKR", "IBKRClient.cs");
            if (File.Exists(candidate)) return File.ReadAllText(candidate);
            dir = Path.GetDirectoryName(dir);
        }
        throw new FileNotFoundException("Could not locate IBKRClient.cs from the test output dir.");
    }

    [Fact]
    public void Every_snapshot_request_is_preceded_by_a_lease()
    {
        var src = ClientSource();
        var lines = src.Split('\n');

        var offenders = lines
            .Select((text, idx) => (text, lineNo: idx + 1))
            .Where(l => l.text.Contains("iserver/marketdata/snapshot?conids=",
                                        StringComparison.Ordinal))
            // Doc comments describe the endpoint; they do not subscribe to it.
            .Where(l => !l.text.TrimStart().StartsWith("//", StringComparison.Ordinal))
            .Where(l =>
            {
                // Walk back to the enclosing method and require a lease between
                // here and there. 60 lines comfortably spans the longest of the
                // three call sites (the chunked option path).
                var from = Math.Max(0, l.lineNo - 60);
                var window = string.Join("\n", lines[from..(l.lineNo - 1)]);
                return !window.Contains("LeaseLinesAsync", StringComparison.Ordinal);
            })
            .Select(l => $"  line {l.lineNo}: {l.text.Trim()}")
            .ToList();

        Assert.True(offenders.Count == 0,
            "These marketdata/snapshot calls SUBSCRIBE without taking a line lease, "
            + "so the lines they open are never released:\n"
            + string.Join("\n", offenders)
            + "\n\nWrap the call in `await using var lease = await LeaseLinesAsync(conids, \"...\", ct);`");
    }

    [Fact]
    public void The_unsubscribe_that_releases_a_line_still_exists()
    {
        // Guards against the lease surviving as bookkeeping while the call that
        // actually frees the line at IBKR is refactored away — budget returned,
        // real lines still held, outage back and the counters saying we are fine.
        var src = ClientSource();
        Assert.Contains("marketdata/{conid}/unsubscribe", src, StringComparison.Ordinal);
    }

    [Fact]
    public void Unsubscribe_does_not_ride_on_the_callers_cancellation_token()
    {
        // The release runs on the way OUT, frequently when the caller's token is
        // already cancelled — a timed-out pass. Passing that token would skip
        // the unsubscribe in exactly the case that leaks the most lines.
        var src = ClientSource();
        var at = src.IndexOf("marketdata/{conid}/unsubscribe", StringComparison.Ordinal);
        Assert.True(at > 0, "unsubscribe call not found");
        var window = src.Substring(at, Math.Min(240, src.Length - at));
        Assert.Contains("CancellationToken.None", window, StringComparison.Ordinal);
    }
}
