using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Admin;

/// <summary>
/// Pure unit tests for DataTrustEndpoints.LastCompletedUsSession — the
/// freshness reference used by the bar-cache/quality gate. No Postgres,
/// no fixture: this is a deterministic time-zone/session calculation.
///
/// The bug this guards: measuring "days behind" against the wall-clock
/// calendar day makes a normal intraday 1-day daily lag read as -1d /
/// STALE. Freshness must be measured against the last COMPLETED US
/// regular session instead, so "we already have the latest bar that
/// can exist" reads as 0-behind.
/// </summary>
public sealed class MarketSessionTest
{
    // NYSE regular close is 16:00 ET (20:00 UTC in summer / EDT, 21:00
    // UTC in winter / EST). We pick unambiguous UTC instants and assert
    // the session date the gate should anchor freshness to.

    [Fact]
    public void MidSession_returns_prior_trading_day()
    {
        // Thu 2026-07-16 17:30 UTC = 13:30 ET — market OPEN, today's
        // daily bar does not exist yet, so the latest complete session
        // is Wednesday 2026-07-15.
        var utc = new DateTime(2026, 7, 16, 17, 30, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 15),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }

    [Fact]
    public void AfterClose_returns_today()
    {
        // Thu 2026-07-16 20:45 UTC = 16:45 ET — past the close + settle
        // buffer, so today's session (Thu) is complete.
        var utc = new DateTime(2026, 7, 16, 20, 45, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 16),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }

    [Fact]
    public void RightAtClose_not_yet_complete_uses_prior_day()
    {
        // Thu 2026-07-16 20:05 UTC = 16:05 ET — bell has rung but inside
        // the settle buffer (complete at 16:15 ET), so not yet counted.
        var utc = new DateTime(2026, 7, 16, 20, 5, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 15),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }

    [Fact]
    public void Saturday_returns_friday()
    {
        // Sat 2026-07-18 12:00 UTC — weekend, last session is Fri 07-17.
        var utc = new DateTime(2026, 7, 18, 12, 0, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 17),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }

    [Fact]
    public void Sunday_returns_friday()
    {
        var utc = new DateTime(2026, 7, 19, 23, 0, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 17),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }

    [Fact]
    public void MondayPreClose_returns_friday()
    {
        // Mon 2026-07-20 14:00 UTC = 10:00 ET — market open, Monday's
        // bar not complete, weekend has no sessions → back to Fri 07-17.
        var utc = new DateTime(2026, 7, 20, 14, 0, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 17),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }

    [Fact]
    public void EarlyMorningUtc_before_ET_midnight_rolls_correctly()
    {
        // Fri 2026-07-17 02:00 UTC = Thu 2026-07-16 22:00 ET — still
        // Thursday evening in New York, after Thursday's close, so the
        // last completed session is Thursday 07-16 (guards the UTC/ET
        // date-line: naive UTC .Date would wrongly say Friday).
        var utc = new DateTime(2026, 7, 17, 2, 0, 0, DateTimeKind.Utc);
        Assert.Equal(new DateOnly(2026, 7, 16),
            DataTrustEndpoints.LastCompletedUsSession(utc));
    }
}
