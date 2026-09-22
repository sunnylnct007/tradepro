using System;
using System.IO;
using Xunit;

namespace TradePro.Api.Tests.Admin;

/// <summary>
/// A lane that has been rescoped has NOT stopped running, and must not say so.
///
/// Seen live 22 Sep 2026. The 5m lane was legitimately rescoped from 969 symbols
/// to the 12 that actually consume 5m data. Every correct new-size run fell
/// under the 75th-percentile "lane-sized" floor and was skipped for grading, so
/// the banner reported:
///
///     bars_5m: has not run for 24h (expected within 3h)
///           ... ignored 22 partial fetch(es) (single-symbol cache misses
///               are not lane coverage)
///
/// It had run ten minutes earlier, and 22 times since the rescope. Two false
/// statements in one line: it HAD run, and those runs were not single-symbol
/// cache misses.
///
/// THE PERCENTILE IS NOT THE BUG and must not be "fixed". Its own comment
/// documents surviving this exact scenario once before, and it self-heals as the
/// 60-row window fills with new-size runs. Only the prose was wrong — which is
/// worse than a wrong threshold, because a banner that states a falsehood
/// teaches the operator to stop reading it.
/// </summary>
public class DataReadinessSaysWhatIsTrueTest
{
    private static string Src()
    {
        var dir = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && dir is not null; i++)
        {
            var c = Path.Combine(dir, "TradePro.Api", "Endpoints",
                                 "DataReadinessEndpoints.cs");
            if (File.Exists(c)) return File.ReadAllText(c);
            dir = Path.GetDirectoryName(dir);
        }
        throw new FileNotFoundException("DataReadinessEndpoints.cs not found");
    }

    [Fact]
    public void A_lane_with_skipped_partials_is_not_called_stopped()
    {
        var s = Src();
        Assert.Contains("last FULL-LANE harvest was", s, StringComparison.Ordinal);
        Assert.Contains("the lane IS running", s, StringComparison.Ordinal);
    }

    [Fact]
    public void The_stopped_wording_survives_for_a_lane_that_really_stopped()
    {
        // With no partials skipped, "has not run" is the truth and must stay.
        var s = Src();
        Assert.Contains("$\"has not run for {ageH:F0}h\"", s, StringComparison.Ordinal);
    }

    [Fact]
    public void Skipped_runs_are_not_mislabelled_as_single_symbol_cache_misses()
    {
        // True of the ad-hoc fetches the guard was built for; false of a
        // rescoped lane. Naming the cause wrongly sends the reader hunting a
        // bug that is not there.
        var s = Src();
        Assert.DoesNotContain("single-symbol cache misses are not lane coverage",
                              s, StringComparison.Ordinal);
        Assert.Contains("or a lane that has been rescoped", s, StringComparison.Ordinal);
    }

    [Fact]
    public void The_message_states_the_lane_size_it_compared_against()
    {
        // "covered fewer symbols" with no number is a mood, not a warning.
        var s = Src();
        Assert.Contains("than this lane's typical {laneSize}", s, StringComparison.Ordinal);
    }

    [Fact]
    public void The_percentile_baseline_is_untouched()
    {
        // The threshold is NOT the bug. It already survived this scenario once
        // and self-heals; changing it here would be fixing the wrong thing.
        var s = Src();
        Assert.Contains("THE BASELINE IS A PERCENTILE, NOT THE MAXIMUM",
                        s, StringComparison.Ordinal);
        Assert.Contains("Math.Floor(sorted.Count * 0.75)", s, StringComparison.Ordinal);
    }
}
