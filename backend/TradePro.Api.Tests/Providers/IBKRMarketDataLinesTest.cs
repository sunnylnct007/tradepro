using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The market-data LINE BUDGET for the one shared IBKR session.
///
/// Context these tests are defending: /iserver/marketdata/snapshot SUBSCRIBES,
/// nothing in this repo unsubscribed before 16 Sep, and past IBKR's cap the
/// session serves EMPTY FIELDS rather than errors — which reached the desk as
/// "no strikes" on all six markets at 14:12:21Z and fixed itself 45 minutes
/// later. A leaked line here does not throw; it comes back days later as a
/// blank chain. So these tests are mostly about the leak paths.
/// </summary>
public class IBKRMarketDataLinesTest
{
    [Fact]
    public void A_budget_below_one_is_refused_not_silently_clamped()
    {
        // Clamping to 1 would "work" and then serialise the entire desk behind
        // a single line. A misconfiguration must be loud at startup.
        Assert.Throws<ArgumentOutOfRangeException>(() => new IBKRMarketDataLines(0));
        Assert.Throws<ArgumentOutOfRangeException>(() => new IBKRMarketDataLines(-5));
    }

    [Fact]
    public async Task Lines_under_budget_are_granted_without_queuing()
    {
        var lines = new IBKRMarketDataLines(50);
        await lines.AcquireAsync(30);
        Assert.Equal(30, lines.InUse);
        Assert.Equal(0, lines.Waiting);
        Assert.Equal((1L, 0L), lines.Stats);
    }

    [Fact]
    public async Task A_caller_over_budget_WAITS_rather_than_oversubscribing()
    {
        // The whole point. Before this, the second caller just piled its conids
        // onto the same session and IBKR started answering blanks.
        var lines = new IBKRMarketDataLines(50);
        await lines.AcquireAsync(40);

        var second = lines.AcquireAsync(30);
        Assert.False(second.IsCompleted);
        Assert.Equal(1, lines.Waiting);
        Assert.Equal(40, lines.InUse);      // NOT 70

        lines.Release(40);
        await second;
        Assert.Equal(30, lines.InUse);
    }

    [Fact]
    public async Task Release_admits_every_waiter_that_now_fits()
    {
        var lines = new IBKRMarketDataLines(100);
        await lines.AcquireAsync(100);
        var a = lines.AcquireAsync(20);
        var b = lines.AcquireAsync(30);
        var c = lines.AcquireAsync(40);

        lines.Release(100);
        await Task.WhenAll(a, b, c);
        Assert.Equal(90, lines.InUse);
        Assert.Equal(0, lines.Waiting);
    }

    [Fact]
    public async Task A_stream_of_small_asks_cannot_starve_the_big_one()
    {
        // The big one is the strangle's chain solve. If small 15-minute pollers
        // could jump a queued placement it would be starved at exactly 14:12Z,
        // which is the failure we are fixing — so waiters queue FIFO even when
        // capacity momentarily exists.
        var lines = new IBKRMarketDataLines(50);
        await lines.AcquireAsync(50);

        var big = lines.AcquireAsync(50);
        var small = lines.AcquireAsync(1);

        lines.Release(50);
        await big;
        Assert.False(small.IsCompleted);     // small did NOT overtake
        Assert.Equal(50, lines.InUse);

        lines.Release(50);
        await small;
    }

    [Fact]
    public async Task An_ask_larger_than_the_whole_budget_is_clamped_not_deadlocked()
    {
        // A caller whose chunk size someone later raises above the ceiling must
        // run slowly, never hang forever. A permanent hang here would present
        // as the blank chain we are removing — reintroduced from our own side.
        var lines = new IBKRMarketDataLines(10);
        await lines.AcquireAsync(500).WaitAsync(TimeSpan.FromSeconds(2));
        Assert.Equal(10, lines.InUse);
        lines.Release(500);
        Assert.Equal(0, lines.InUse);
    }

    [Fact]
    public async Task Cancelling_while_queued_does_not_strand_the_lines()
    {
        // A pass that times out mid-queue must not hold budget forever. This is
        // the leak that would quietly re-create the outage over a few days.
        var lines = new IBKRMarketDataLines(50);
        await lines.AcquireAsync(50);

        using var cts = new CancellationTokenSource();
        var doomed = lines.AcquireAsync(10, cts.Token);
        Assert.Equal(1, lines.Waiting);

        cts.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => doomed);
        Assert.Equal(0, lines.Waiting);
        Assert.Equal(50, lines.InUse);   // the cancelled caller took nothing

        lines.Release(50);
        Assert.Equal(0, lines.InUse);
    }

    [Fact]
    public async Task A_cancelled_waiter_does_not_block_the_one_behind_it()
    {
        var lines = new IBKRMarketDataLines(50);
        await lines.AcquireAsync(50);

        using var cts = new CancellationTokenSource();
        var doomed = lines.AcquireAsync(50, cts.Token);
        var behind = lines.AcquireAsync(10);

        cts.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => doomed);

        lines.Release(50);
        await behind.WaitAsync(TimeSpan.FromSeconds(2));
        Assert.Equal(10, lines.InUse);
    }

    [Fact]
    public void Release_can_never_drive_the_count_negative()
    {
        // A double-release (lease disposed twice, or a retry path releasing a
        // second time) would otherwise mint free budget out of nothing and
        // silently restore the oversubscription.
        var lines = new IBKRMarketDataLines(50);
        lines.Release(20);
        Assert.Equal(0, lines.InUse);
    }

    [Fact]
    public async Task Concurrent_traffic_never_exceeds_the_ceiling()
    {
        // Nine callers is this desk's real shape: 6 Mac daemons + Lambda jobs
        // + the MCP endpoint, all through the one session.
        const int Max = 40;
        var lines = new IBKRMarketDataLines(Max);
        var peak = 0;
        var peakLock = new object();

        var workers = Enumerable.Range(0, 9).Select(async i =>
        {
            for (var pass = 0; pass < 12; pass++)
            {
                var n = 1 + ((i * 7 + pass * 3) % 15);
                await lines.AcquireAsync(n);
                lock (peakLock) peak = Math.Max(peak, lines.InUse);
                await Task.Yield();
                lines.Release(n);
            }
        }).ToArray();

        await Task.WhenAll(workers);
        Assert.True(peak <= Max, $"peak {peak} exceeded the {Max}-line ceiling");
        Assert.Equal(0, lines.InUse);
        Assert.Equal(0, lines.Waiting);
    }

    [Fact]
    public async Task Stats_report_how_often_we_had_to_queue()
    {
        // A rising queued share is the signal that the SCHEDULE is too dense,
        // not that the ceiling is too low. Raising the ceiling instead is how
        // we would walk straight back into empty fields.
        var lines = new IBKRMarketDataLines(10);
        await lines.AcquireAsync(10);
        var queued = lines.AcquireAsync(10);
        lines.Release(10);
        await queued;

        var (granted, queuedCount) = lines.Stats;
        Assert.Equal(2, granted);
        Assert.Equal(1, queuedCount);
    }

    [Fact]
    public async Task Zero_conid_asks_are_free()
    {
        var lines = new IBKRMarketDataLines(5);
        await lines.AcquireAsync(0);
        Assert.Equal(0, lines.InUse);
        Assert.Equal((0L, 0L), lines.Stats);
    }
}
