using TradePro.Api.Endpoints;
using Xunit;

namespace TradePro.Api.Tests.Endpoints;

/// <summary>
/// Pure-logic coverage for the wheel position-management rules (9 Aug 2026,
/// owner: "we have 75% recovered from the premium, now we can roll it off to
/// a better one"): premium-captured math, the close/roll suggestion, and the
/// expiry→IBKR-month-label mapping the live-mark lookup depends on.
/// </summary>
public class WheelWatchdogTest
{
    // ── ExpiryToMonthLabel ───────────────────────────────────────────

    [Theory]
    [InlineData(2026, 9, 18, "SEP26")]
    [InlineData(2026, 12, 18, "DEC26")]
    [InlineData(2027, 1, 15, "JAN27")]
    public void Expiry_maps_to_ibkr_month_label(int y, int m, int d, string expected)
        => Assert.Equal(expected, OptionsEndpoints.ExpiryToMonthLabel(new DateOnly(y, m, d)));

    // ── PremiumCapturedPct ───────────────────────────────────────────

    [Fact]
    public void Captured_pct_is_share_of_entry_premium_decayed()
    {
        // Sold at 2.00, now marks 0.50 → 75% captured.
        Assert.Equal(75.0m, OptionsEndpoints.PremiumCapturedPct(2.00m, 0.50m));
    }

    [Fact]
    public void Underwater_short_reports_negative_capture()
    {
        // Sold at 1.00, now marks 1.80 → -80% (losing) — visible, not clamped.
        Assert.Equal(-80.0m, OptionsEndpoints.PremiumCapturedPct(1.00m, 1.80m));
    }

    [Theory]
    [InlineData(null, 0.5)]     // no entry premium recorded
    [InlineData(0.0, 0.5)]      // zero entry premium — division guard
    [InlineData(-1.0, 0.5)]     // nonsense entry premium
    public void Missing_or_invalid_entry_premium_returns_null(double? entry, double mark)
        => Assert.Null(OptionsEndpoints.PremiumCapturedPct((decimal?)entry, (decimal)mark));

    [Fact]
    public void Missing_mark_returns_null_not_zero()
        => Assert.Null(OptionsEndpoints.PremiumCapturedPct(2.00m, null));

    // ── RollSuggestion ───────────────────────────────────────────────

    [Fact]
    public void At_or_above_target_suggests_close_and_redeploy()
    {
        Assert.Equal("CLOSE_AND_REDEPLOY", OptionsEndpoints.RollSuggestion(75.0m, "OTM", 75m));
        Assert.Equal("CLOSE_AND_REDEPLOY", OptionsEndpoints.RollSuggestion(92.3m, "OTM", 75m));
    }

    [Fact]
    public void Below_target_and_otm_suggests_nothing()
        => Assert.Null(OptionsEndpoints.RollSuggestion(40.0m, "OTM", 75m));

    [Fact]
    public void Itm_overrides_capture_with_roll_or_assignment()
    {
        // Even a profitable short that has gone ITM is a roll/assignment
        // decision first — capture % alone must not say "close and redeploy".
        Assert.Equal("ROLL_OR_ACCEPT_ASSIGNMENT",
            OptionsEndpoints.RollSuggestion(80.0m, "ITM (assignment risk)", 75m));
        Assert.Equal("ROLL_OR_ACCEPT_ASSIGNMENT",
            OptionsEndpoints.RollSuggestion(null, "ITM (assignment risk)", 75m));
    }

    [Fact]
    public void No_capture_data_and_otm_suggests_nothing()
        => Assert.Null(OptionsEndpoints.RollSuggestion(null, "OTM", 75m));
}
