using System;
using System.IO;
using System.Linq;
using Xunit;

namespace TradePro.Api.Tests.Risk;

/// <summary>
/// The risk gate must not refuse an EXIT because the regular session is shut.
///
/// THE DAMAGE, measured 21 Sep 2026. mean_reversion_swing_ibkr had 12 positions
/// open and had closed NONE — 47 exit attempts, zero reaching the broker:
///
///   gate     : market_closed
///   decision : BLOCKED
///   side     : SELL
///   reason   : US equity market is closed — refusing ARWR_US_EQ
///
/// Every one then cancelled "superseded by newer order" as the */15 daemon
/// raised a replacement to be blocked in turn.
///
/// WHY IT SURVIVED A FIX. The strategy-side guard was corrected on 20 Sep
/// (Strategy.placeable_now grew is_exit). This gate is a SECOND, INDEPENDENT
/// copy of the same rule. One site was fixed; the other kept refusing — the
/// duplicate-definition shape that is this codebase's most common bug. Hence a
/// test pinned to THIS file rather than trusting the Python one.
///
/// The asymmetry is deliberate: an entry deferred is a missed opportunity, an
/// EXIT deferred is an open risk nobody chose to keep. Gate 3c in the same file
/// already says so — "exits/sells reduce exposure and must never be blocked".
/// </summary>
public class ExitsAreNotEntriesTest
{
    private static string Src()
    {
        var dir = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && dir is not null; i++)
        {
            var c = Path.Combine(dir, "TradePro.Api", "Risk", "RiskGate.cs");
            if (File.Exists(c)) return File.ReadAllText(c);
            dir = Path.GetDirectoryName(dir);
        }
        throw new FileNotFoundException("RiskGate.cs not found");
    }

    /// The US-equity market-hours block, as source.
    private static string UsEquityBlock()
    {
        var s = Src();
        var i = s.IndexOf("if (IsUsEquitySymbol(order.Symbol))", StringComparison.Ordinal);
        Assert.True(i > 0,
            "the US-equity market-hours gate could not be found — if it was "
            + "restructured, re-point this test rather than deleting it: it "
            + "guards 47 lost exits.");
        return s.Substring(i, Math.Min(1800, s.Length - i));
    }

    [Fact]
    public void An_exit_is_distinguished_from_an_entry()
    {
        var b = UsEquityBlock();
        Assert.Contains("isExit", b, StringComparison.Ordinal);
        Assert.Contains("\"SELL\"", b, StringComparison.Ordinal);
    }

    [Fact]
    public void Only_an_ENTRY_is_held_outside_the_regular_session()
    {
        // The refusal that remains must be reachable ONLY for a non-exit.
        var b = UsEquityBlock();
        Assert.Contains("!isExit && !UsEquityMarketOpenUtc", b, StringComparison.Ordinal);
    }

    [Fact]
    public void A_non_trading_day_still_refuses_BOTH()
    {
        // Saturday fills nothing, exit or not. Removing this would re-create
        // the 34 weekend orders of 19 Sep.
        var b = UsEquityBlock();
        Assert.Contains("UsEquityTradingDayUtc", b, StringComparison.Ordinal);
        Assert.Contains("weekend/holiday", b, StringComparison.Ordinal);
    }

    [Fact]
    public void The_entry_refusal_SAYS_an_exit_would_have_passed()
    {
        // Otherwise the operator cannot tell why their close went through and
        // their buy did not, which is how this stayed invisible for weeks.
        var b = UsEquityBlock();
        Assert.Contains("EXIT would be allowed through", b, StringComparison.Ordinal);
    }

    [Fact]
    public void The_trading_day_helper_states_that_holidays_are_not_modelled()
    {
        // Claiming a holiday calendar we do not have would be worse than the
        // weekday check it really is.
        var s = Src();
        // The DECLARATION, not the call site — the doc comment sits above the
        // method, and the first occurrence of the name is where it is used.
        var i = s.IndexOf("private static bool UsEquityTradingDayUtc", StringComparison.Ordinal);
        Assert.True(i > 0, "UsEquityTradingDayUtc is not declared");
        var doc = s.Substring(Math.Max(0, i - 1200), 1200);
        Assert.Contains("Holidays are NOT modelled", doc, StringComparison.Ordinal);
    }

    [Fact]
    public void The_fx_weekend_backstop_is_untouched()
    {
        // Adding an exit path must not weaken the FX guard that stopped the
        // weekend duplicate flood.
        var s = Src();
        Assert.Contains("IsFxSymbol(order.Symbol) && !FxMarketOpenUtc", s, StringComparison.Ordinal);
    }
}
