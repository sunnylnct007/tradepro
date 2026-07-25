using TradePro.Api.Oms;
using Xunit;
using SA = TradePro.Api.Oms.PositionReconcile.StandingAction;

namespace TradePro.Api.Tests.Oms;

/// <summary>
/// Safety coverage for the standing-position reconcile decision — the rule that
/// governs which drift the background reconciler may AUTO-MUTATE. This is the
/// fix for phantom shorts silently accumulating in the OMS (the IBKR clone's
/// ack-less gateway wrote unconfirmed fills a broker reset could not clear). The
/// decision must be conservative: only auto-clear a demo book the broker holds
/// exactly zero of, never touch a confirmed-execution book, never race a fresh
/// fill.
/// </summary>
public class StandingReconcileTest
{
    static readonly System.TimeSpan Grace = System.TimeSpan.FromMinutes(5);
    static readonly System.DateTime Now = new(2026, 7, 26, 12, 0, 0, System.DateTimeKind.Utc);
    static readonly System.DateTime Old = Now.AddHours(-1);   // past the grace window

    static SA Classify(decimal oms, decimal broker, bool canAutoClear, System.DateTime? lastFill = null)
        => PositionReconcile.ClassifyStanding(oms, broker, lastFill ?? Old, canAutoClear, Grace, Now);

    [Fact]
    public void Equal_qty_is_in_sync()
    {
        Assert.Equal(SA.InSync, Classify(oms: -3m, broker: -3m, canAutoClear: true));
        Assert.Equal(SA.InSync, Classify(oms: 0m, broker: 0m, canAutoClear: true));
    }

    [Fact]
    public void Phantom_short_on_demo_broker_flat_is_auto_cleared()
    {
        // The ANET/COST case: OMS holds a short, broker holds NONE, demo opted in.
        Assert.Equal(SA.AutoClear, Classify(oms: -1m, broker: 0m, canAutoClear: true));
        Assert.Equal(SA.AutoClear, Classify(oms: -142m, broker: 0m, canAutoClear: true));
        Assert.Equal(SA.AutoClear, Classify(oms: 5m, broker: 0m, canAutoClear: true));   // phantom long too
    }

    [Fact]
    public void Broker_flat_but_source_not_opted_in_only_flags()
    {
        // A confirmed-execution / real-money book (canAutoClear=false) is NEVER
        // auto-mutated — even a broker-flat mismatch is surfaced, not cleared.
        Assert.Equal(SA.Flag, Classify(oms: -1m, broker: 0m, canAutoClear: false));
    }

    [Fact]
    public void Partial_mismatch_is_flagged_never_auto_cleared()
    {
        // Broker holds SOME (not zero) → ambiguous residual → flag, never guess,
        // even on an opted-in demo source.
        Assert.Equal(SA.Flag, Classify(oms: -5m, broker: -2m, canAutoClear: true));
        Assert.Equal(SA.Flag, Classify(oms: 10m, broker: 3m, canAutoClear: true));
        Assert.Equal(SA.Flag, Classify(oms: 2m, broker: -2m, canAutoClear: true));   // opposite sign
    }

    [Fact]
    public void Recently_filled_is_skipped_to_not_race_settlement()
    {
        // A fill inside the grace window may still be settling at the broker —
        // never reverse it, even when it looks like broker-flat drift.
        Assert.Equal(SA.SkipGrace, Classify(oms: -1m, broker: 0m, canAutoClear: true, lastFill: Now.AddMinutes(-1)));
    }
}
