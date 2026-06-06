using TradePro.Api.Positions;
using Xunit;

namespace TradePro.Api.Tests.Positions;

/// <summary>
/// Pure-core coverage for the per-strategy P&amp;L attribution helpers
/// (<see cref="PnlByStrategy"/>) behind GET /api/pnl/by-strategy. The endpoint
/// plumbing is thin; the part that must be PROVABLY correct — the long-only
/// FIFO realised match that stands in for T212's missing realised feed, plus
/// the null-safe total / note joining — is unit-tested here with no DB / HTTP.
/// </summary>
public class PnlByStrategyTest
{
    private static PnlByStrategy.Fill Buy(decimal q, decimal p) => new("BUY", q, p);
    private static PnlByStrategy.Fill Sell(decimal q, decimal p) => new("SELL", q, p);

    // ── FIFO realised ─────────────────────────────────────────────────────
    [Fact]
    public void Fifo_simple_roundtrip_banks_the_difference()
    {
        // Buy 10 @ 100, sell 10 @ 110 → +100 realised, 1 closed, 1 win.
        var r = PnlByStrategy.FifoRealised(new[] { Buy(10, 100m), Sell(10, 110m) });
        Assert.Equal(100m, r.Realised);
        Assert.Equal(1, r.ClosedTrades);
        Assert.Equal(1, r.WinningTrades);
        Assert.Equal(0m, r.UnmatchedSellQty);
        Assert.Equal(100.0, r.WinRatePct);
    }

    [Fact]
    public void Fifo_matches_oldest_lots_first()
    {
        // Buy 10 @ 100, buy 10 @ 120, sell 15 @ 130.
        // FIFO: 10 from the 100 lot (+300) + 5 from the 120 lot (+50) = +350.
        var r = PnlByStrategy.FifoRealised(new[]
        {
            Buy(10, 100m), Buy(10, 120m), Sell(15, 130m),
        });
        Assert.Equal(350m, r.Realised);
        Assert.Equal(1, r.ClosedTrades);
        Assert.Equal(1, r.WinningTrades);
        Assert.Equal(0m, r.UnmatchedSellQty);
    }

    [Fact]
    public void Fifo_counts_a_losing_sell_as_a_closed_non_win()
    {
        // Win then loss → 2 closed, 1 win = 50% win rate; net realised −50.
        var r = PnlByStrategy.FifoRealised(new[]
        {
            Buy(10, 100m), Sell(10, 110m),   // +100
            Buy(10, 100m), Sell(10, 85m),    // −150
        });
        Assert.Equal(-50m, r.Realised);
        Assert.Equal(2, r.ClosedTrades);
        Assert.Equal(1, r.WinningTrades);
        Assert.Equal(50.0, r.WinRatePct);
    }

    [Fact]
    public void Fifo_reports_unmatched_sell_qty_and_never_fabricates_a_basis()
    {
        // Sell 5 with only 3 bought → 3 matched (+30), 2 unmatched (NOT a
        // zero-cost +500 fabrication).
        var r = PnlByStrategy.FifoRealised(new[] { Buy(3, 100m), Sell(5, 110m) });
        Assert.Equal(30m, r.Realised);
        Assert.Equal(1, r.ClosedTrades);
        Assert.Equal(2m, r.UnmatchedSellQty);
    }

    [Fact]
    public void Fifo_with_no_sells_realises_nothing_and_winrate_is_null()
    {
        var r = PnlByStrategy.FifoRealised(new[] { Buy(10, 100m), Buy(5, 90m) });
        Assert.Equal(0m, r.Realised);
        Assert.Equal(0, r.ClosedTrades);
        Assert.Null(r.WinRatePct);   // empty closed-set → n/a, never 0%
    }

    [Fact]
    public void Fifo_ignores_non_positive_qty_fills()
    {
        var r = PnlByStrategy.FifoRealised(new[]
        {
            Buy(0m, 100m), Buy(10m, 100m), Sell(-1m, 110m), Sell(10m, 110m),
        });
        Assert.Equal(100m, r.Realised);
        Assert.Equal(1, r.ClosedTrades);
    }

    // ── FIFO realised SPLIT (today vs life-to-date) ───────────────────────
    private static PnlByStrategy.DatedFill BuyD(decimal q, decimal p, bool today) => new("BUY", q, p, today);
    private static PnlByStrategy.DatedFill SellD(decimal q, decimal p, bool today) => new("SELL", q, p, today);

    [Fact]
    public void Split_attributes_realised_to_the_day_of_the_closing_sell()
    {
        // Buy long ago, sell today → all +100 is banked TODAY, and also LTD.
        var r = PnlByStrategy.FifoRealisedSplit(new[]
        {
            BuyD(10, 100m, today: false),
            SellD(10, 110m, today: true),
        });
        Assert.Equal(100m, r.RealisedLtd);
        Assert.Equal(100m, r.RealisedToday);
        Assert.Equal(1, r.ClosedTrades);
    }

    [Fact]
    public void Split_only_today_sells_count_toward_realised_today()
    {
        // A prior sell (+100, not today) + a sell today (−50). LTD = +50,
        // today = −50. The matched BUY lot's date is irrelevant — only the
        // SELL's day decides attribution.
        var r = PnlByStrategy.FifoRealisedSplit(new[]
        {
            BuyD(10, 100m, today: false), SellD(10, 110m, today: false),  // +100, banked earlier
            BuyD(10, 100m, today: false), SellD(10, 95m, today: true),    // −50, banked today
        });
        Assert.Equal(50m, r.RealisedLtd);
        Assert.Equal(-50m, r.RealisedToday);
        Assert.Equal(2, r.ClosedTrades);
        Assert.Equal(1, r.WinningTrades);
        Assert.Equal(50.0, r.WinRatePct);
    }

    [Fact]
    public void Split_with_no_today_sells_banks_zero_today_but_full_ltd()
    {
        var r = PnlByStrategy.FifoRealisedSplit(new[]
        {
            BuyD(10, 100m, today: false), SellD(10, 130m, today: false),
        });
        Assert.Equal(300m, r.RealisedLtd);
        Assert.Equal(0m, r.RealisedToday);   // banked, but not today
    }

    [Fact]
    public void Split_reports_unmatched_sell_qty_without_fabricating_a_basis()
    {
        // Sell 5 today with only 3 bought → 3 matched (+30, today), 2 unmatched.
        var r = PnlByStrategy.FifoRealisedSplit(new[] { BuyD(3, 100m, false), SellD(5, 110m, true) });
        Assert.Equal(30m, r.RealisedLtd);
        Assert.Equal(30m, r.RealisedToday);
        Assert.Equal(2m, r.UnmatchedSellQty);
    }

    [Fact]
    public void Split_with_no_sells_realises_nothing_and_winrate_is_null()
    {
        var r = PnlByStrategy.FifoRealisedSplit(new[] { BuyD(10, 100m, true), BuyD(5, 90m, true) });
        Assert.Equal(0m, r.RealisedLtd);
        Assert.Equal(0m, r.RealisedToday);
        Assert.Equal(0, r.ClosedTrades);
        Assert.Null(r.WinRatePct);
    }

    // ── Total (null-safe, same-currency only) ─────────────────────────────
    [Fact]
    public void Total_is_open_plus_realised_only_when_both_present()
    {
        Assert.Equal(150m, PnlByStrategy.Total(100m, 50m));
        Assert.Null(PnlByStrategy.Total(100m, null));
        Assert.Null(PnlByStrategy.Total(null, 50m));
        Assert.Null(PnlByStrategy.Total(null, null));
    }

    // ── JoinNotes ─────────────────────────────────────────────────────────
    [Fact]
    public void JoinNotes_drops_blanks_and_dedupes()
    {
        Assert.Equal("a; b", PnlByStrategy.JoinNotes("a", null, "", "  ", "b", "a"));
        Assert.Equal("", PnlByStrategy.JoinNotes(null, "", "   "));
    }
}
