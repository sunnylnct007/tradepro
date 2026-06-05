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
