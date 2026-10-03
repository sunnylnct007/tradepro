using TradePro.Api.Providers.IBKR;
using Xunit;

namespace TradePro.Api.Tests.Providers;

/// <summary>
/// The market-data line budget, and the high-water mark that makes a dark
/// snapshot diagnosable after the fact.
///
/// THE PROBLEM THIS MEASURES. /iserver/marketdata/snapshot SUBSCRIBES, it does
/// not read. Over the cap IBKR stops erroring and starts serving EMPTY FIELDS,
/// which reaches callers as "no strikes" / "could not read spot" and is
/// indistinguishable from a session someone else took. On 2 Oct 2026, 13% of
/// health probes said "auth VALID but snapshot DARK" and nothing recorded
/// whether we had exhausted our own budget doing it to ourselves.
///
/// `InUse` cannot answer that question later: by the time anyone asks, the
/// leases have drained and it reads 0. Hence a peak that survives.
/// </summary>
public class IBKRMarketDataLinesTest
{
    [Fact]
    public void Peak_survives_the_drain()
    {
        var lines = new IBKRMarketDataLines(10);
        lines.AcquireAsync(7).Wait();
        Assert.Equal(7, lines.InUse);
        Assert.Equal(7, lines.HighWater.Peak);

        lines.Release(7);

        // The live gauge goes back to zero — and this is exactly why it is
        // useless for diagnosing a snapshot that went dark ten minutes ago.
        Assert.Equal(0, lines.InUse);
        Assert.Equal(7, lines.HighWater.Peak);
        Assert.NotNull(lines.HighWater.AtUtc);
    }

    [Fact]
    public void Peak_tracks_the_maximum_not_the_latest()
    {
        var lines = new IBKRMarketDataLines(10);
        lines.AcquireAsync(8).Wait();
        lines.Release(8);
        lines.AcquireAsync(2).Wait();

        Assert.Equal(8, lines.HighWater.Peak);
        lines.Release(2);
    }

    [Fact]
    public void Saturation_is_visible_as_full_utilisation()
    {
        var lines = new IBKRMarketDataLines(10);
        lines.AcquireAsync(10).Wait();
        Assert.Equal(1.0, lines.PeakUtilisation);
        lines.Release(10);
        // Still 1.0 after release — the saturation DID happen, and that is the
        // fact a later investigation needs.
        Assert.Equal(1.0, lines.PeakUtilisation);
    }

    [Fact]
    public void Peak_is_recorded_when_a_QUEUED_waiter_is_released_too()
    {
        // The second raise site. A waiter promoted into the budget raises
        // _inUse just as a direct grant does, and recording only the direct
        // path would under-report precisely the contended case — the
        // fix-one-site-miss-the-other shape this repo keeps hitting.
        var lines = new IBKRMarketDataLines(10);
        lines.AcquireAsync(10).Wait();          // budget full
        var queued = lines.AcquireAsync(6);     // must wait
        Assert.False(queued.IsCompleted);

        lines.Release(10);                      // promotes the waiter
        queued.Wait();

        Assert.Equal(6, lines.InUse);
        Assert.Equal(10, lines.HighWater.Peak); // the earlier 10 still stands
        Assert.True(lines.Stats.Queued >= 1,
            "a lease that had to wait was not counted as queued — that counter "
            + "is how we tell 'budget too small for the schedule' from 'IBKR "
            + "took the session away'");
        lines.Release(6);
    }

    [Fact]
    public void A_request_larger_than_the_whole_budget_is_clamped_not_refused()
    {
        // Refusing would make a caller whose chunk size someone later raised
        // fail permanently and silently — the blank-chain failure we exist to
        // remove, reintroduced from our own side.
        var lines = new IBKRMarketDataLines(5);
        lines.AcquireAsync(50).Wait();
        Assert.Equal(5, lines.InUse);
        lines.Release(50);
        Assert.Equal(0, lines.InUse);
    }

    [Fact]
    public void Releasing_more_than_was_taken_cannot_drive_the_budget_negative()
    {
        var lines = new IBKRMarketDataLines(5);
        lines.AcquireAsync(2).Wait();
        lines.Release(99);
        Assert.Equal(0, lines.InUse);
    }

    [Fact]
    public void A_budget_below_one_is_refused_because_it_would_deadlock()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new IBKRMarketDataLines(0));
    }
}
