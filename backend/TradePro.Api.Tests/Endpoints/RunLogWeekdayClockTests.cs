using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// The dead-process banner cried wolf every Sunday: a flat 30 wall-hour limit
/// on Mon-Fri lanes meant Friday 21:00 + 30h fired at Sunday 03:00, and the
/// owner opened a desk shouting "Dead: live-portfolio, signal-audit,
/// today-setups" over three lanes that had exited 0 on Friday and were not
/// owed again until Monday. Staleness now runs on the weekday clock.
/// </summary>
public class RunLogWeekdayClockTests
{
    // Fri 18 Sep 2026 21:17 UTC — live-portfolio's actual last run.
    private static readonly DateTime FriEvening = new(2026, 9, 18, 21, 17, 0, DateTimeKind.Utc);

    [Fact]
    public void sunday_morning_is_not_a_dead_process()
    {
        // Sun 20 Sep 07:47 UTC — the exact moment the false alarm reached the owner.
        var sunday = new DateTime(2026, 9, 20, 7, 47, 0, DateTimeKind.Utc);
        var h = RunLogEndpoints.WeekdayHoursBetween(FriEvening, sunday);
        // Only Friday 21:17 -> midnight counts: ~2.7 weekday hours.
        Assert.True(h < 3.0, $"weekday hours = {h}");
        Assert.True(h < 30.0, "must NOT trip the 30h limit on a weekend");
    }

    [Fact]
    public void a_lane_that_skips_monday_still_trips_by_tuesday()
    {
        // If Monday's 21:00 run never happens, Tuesday 04:00 carries
        // ~2.7h (Fri) + 24h (Mon) + 4h (Tue) ≈ 30.7 weekday hours — the alarm
        // fires Tuesday morning instead of never.
        var tuesday = new DateTime(2026, 9, 22, 4, 0, 0, DateTimeKind.Utc);
        Assert.True(RunLogEndpoints.WeekdayHoursBetween(FriEvening, tuesday) > 30.0);
    }

    [Fact]
    public void weekday_to_weekday_equals_wall_clock()
    {
        var mon = new DateTime(2026, 9, 21, 9, 0, 0, DateTimeKind.Utc);
        var tue = new DateTime(2026, 9, 22, 9, 0, 0, DateTimeKind.Utc);
        Assert.Equal(24.0, RunLogEndpoints.WeekdayHoursBetween(mon, tue), 3);
    }

    [Fact]
    public void a_full_weekend_contributes_zero()
    {
        var satStart = new DateTime(2026, 9, 19, 0, 0, 0, DateTimeKind.Utc);
        var monStart = new DateTime(2026, 9, 21, 0, 0, 0, DateTimeKind.Utc);
        Assert.Equal(0.0, RunLogEndpoints.WeekdayHoursBetween(satStart, monStart), 3);
    }

    [Fact]
    public void reversed_or_equal_instants_age_zero()
    {
        Assert.Equal(0.0, RunLogEndpoints.WeekdayHoursBetween(FriEvening, FriEvening), 3);
        Assert.Equal(0.0, RunLogEndpoints.WeekdayHoursBetween(FriEvening, FriEvening.AddHours(-5)), 3);
    }
}
