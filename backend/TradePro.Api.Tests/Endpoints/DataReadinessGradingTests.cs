using System.Collections.Generic;
using System.Linq;
using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// A partial fetch must never grade a whole dataset.
///
/// 30 Aug 2026: bars_1d reported "all 1 symbols covered" and usable:true while
/// the nightly harvest had in fact run 244 symbols, 244 GOLD, zero missing. The
/// swing refresh had done a single-symbol cache-miss fetch, and that wrote the
/// newest run_log row. Coverage is missing/(covered+missing), so 1-of-1 scores
/// 0% missing and grades as perfectly healthy.
///
/// It went three runs without an alarm because fail-open is the silent
/// direction — the same shape as the five monitors found green while broken on
/// 17-18 Aug. This is the dataset feeding every regime, Ichimoku, HV and
/// backtest figure in the system.
/// </summary>
public class DataReadinessGradingTests
{
    [Fact]
    public void SingleSymbolFetch_DoesNotGradeTheLane()
    {
        // newest first: a 1-symbol fetch, then the real 244-symbol harvest
        var (index, ignored) = DataReadinessEndpoints.PickGradedRun(
            new[] { 1, 244, 244, 243 });
        Assert.Equal(1, index);      // graded on the harvest, not the fetch
        Assert.Equal(1, ignored);
    }

    [Fact]
    public void SeveralPartialsInARow_AreAllSkipped()
    {
        var (index, ignored) = DataReadinessEndpoints.PickGradedRun(
            new[] { 1, 2, 1, 244, 244 });
        Assert.Equal(3, index);
        Assert.Equal(3, ignored);
    }

    [Fact]
    public void AHealthyLane_GradesOnItsNewestRun()
    {
        var (index, ignored) = DataReadinessEndpoints.PickGradedRun(
            new[] { 244, 244, 243 });
        Assert.Equal(0, index);
        Assert.Equal(0, ignored);
    }

    [Fact]
    public void AHarvestMissingAFewNames_StillGrades()
    {
        // The floor is deliberately generous — this must NOT be treated as a
        // partial fetch just because a handful of delisted names dropped out.
        var (index, _) = DataReadinessEndpoints.PickGradedRun(
            new[] { 230, 244 });
        Assert.Equal(0, index);
    }

    [Fact]
    public void ALaneThatOnlyEverReportsOneSymbol_HasNoNormalToCompareAgainst()
    {
        // Do not invent a baseline; the caller falls back to existing behaviour.
        var (index, ignored) = DataReadinessEndpoints.PickGradedRun(new[] { 1, 1, 1 });
        Assert.Equal(-1, index);
        Assert.Equal(0, ignored);
    }

    [Fact]
    public void ALaneWhoseUniverseLEGITIMATELYSHRANK_IsNotReportedBroken()
    {
        // REGRESSION, caught against the live deployment minutes after shipping
        // the first version of this fix. bars_5m's history still contains runs
        // of 955 symbols from a universe-resolution bug that was deliberately
        // fixed down to 244. With the baseline taken as the all-time MAX, half
        // of 955 is 478, so every correct 244-symbol run scored as a partial
        // fetch, all were discarded, and a healthy lane reported "has not run
        // for 98h".
        //
        // Newest first: current correct runs, then ad-hoc fetches, then the old
        // oversized ones.
        var history = new List<int>();
        history.AddRange(Enumerable.Repeat(244, 15));   // the lane as it is now
        history.AddRange(Enumerable.Repeat(1, 39));     // incidental fetches
        history.AddRange(Enumerable.Repeat(955, 6));    // the old buggy size
        var (index, ignored) = DataReadinessEndpoints.PickGradedRun(history);
        Assert.Equal(0, index);      // grade on today's real run
        Assert.Equal(0, ignored);
    }

    [Fact]
    public void SmallFetchesDoNotDragTheBaselineDown_HoweverManyThereAre()
    {
        // The other direction: the percentile must still sit above a crowd of
        // single-symbol fetches, or they start grading the lane again.
        var history = new List<int> { 1, 1, 1, 1, 1, 1, 244, 244, 244, 244,
                                      244, 244, 244, 244, 244, 244 };
        var (index, ignored) = DataReadinessEndpoints.PickGradedRun(history);
        Assert.Equal(6, index);
        Assert.Equal(6, ignored);
    }

    [Fact]
    public void EmptyHistory_IsHandled()
    {
        Assert.Equal(-1, DataReadinessEndpoints.PickGradedRun(System.Array.Empty<int>()).Index);
        Assert.Equal(-1, DataReadinessEndpoints.PickGradedRun(null!).Index);
    }
}
