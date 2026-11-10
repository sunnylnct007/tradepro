using TradePro.Api.Positions;
using Xunit;

namespace TradePro.Api.Tests.Positions;

/// <summary>
/// The paper ledger counts only what the broker confirmed, by placement
/// record; everything else is its own line. These tests encode the three
/// failure shapes that made the old number untrustworthy: a reconcile-written
/// fill read as money, an unattributed fill folded into a sleeve, and a sell
/// with no confirmed buy realised against a zero basis.
/// </summary>
public class PaperLedgerTest
{
    static readonly DateTime Now = new(2026, 11, 10, 12, 0, 0, DateTimeKind.Utc);

    static PaperLedger.FillRow F(string? strat, string sym, string side, decimal qty, decimal px,
                                 string? fillId = "exec-1", string? orderId = "100",
                                 DateTime? at = null, string broker = "IBKR_PAPER", string ccy = "USD")
        => new(strat, broker, sym, side, qty, px, ccy, fillId, orderId, at ?? Now.AddDays(-1));

    [Fact]
    public void Classify_treats_only_broker_reported_priced_fills_as_confirmed()
    {
        Assert.Equal(PaperLedger.Origin.BrokerConfirmed, PaperLedger.Classify("00025b45.6a96ea93.01.01", "1069512750", 89.36m));
        Assert.Equal(PaperLedger.Origin.BrokerConfirmed, PaperLedger.Classify("recon:1069512750", "1069512750", 89.36m));
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify("assumed_via_position_reconcile:1069", "1069", 89.36m));
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify("standing_reconcile:AAPL", "1069", 89.36m));
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify("reconcile-abc", "1069", 89.36m));
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify("purge-2026-09-28", "1069", 89.36m));
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify("exec-1", "1069", 0m));          // no price
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify("exec-1", null, 89.36m));       // no broker order
        Assert.Equal(PaperLedger.Origin.Assumed, PaperLedger.Classify(null, "1069", 89.36m));         // simulated PAPER fill
    }

    [Fact]
    public void A_confirmed_round_trip_is_money_and_a_reconcile_fill_is_not()
    {
        var r = PaperLedger.Build(new[]
        {
            F("swing", "KO", "BUY", 10, 89.36m, "exec-a", "1", Now.AddDays(-3)),
            F("swing", "KO", "SELL", 10, 90.36m, "exec-b", "2", Now.AddHours(-1)),   // today
            // The reconciler decided we also held 5 MSFT; nobody confirmed a price.
            F("swing", "MSFT", "BUY", 5, 0m, "assumed_via_position_reconcile:9", "9", Now.AddDays(-2)),
        }, Now);

        var swing = Assert.Single(r.Strategies);
        Assert.Equal(10m, swing.RealisedLtd);
        Assert.Equal(10m, swing.RealisedToday);
        Assert.Equal(1, swing.ClosedTrades);
        Assert.Equal(100.0, swing.WinRatePct);
        Assert.Equal(2, swing.ConfirmedFills);
        Assert.Equal(1, swing.AssumedFills);
        Assert.Equal(5m, swing.AssumedQty);
        Assert.Contains(swing.Notes, n => n.Contains("excluded from money"));
        Assert.Empty(r.Unattributed);
    }

    [Fact]
    public void Fills_with_no_strategy_are_their_own_line_never_a_sleeves()
    {
        var r = PaperLedger.Build(new[]
        {
            F("swing", "KO", "BUY", 10, 89m, "e1", "1"),
            F(null, "ESNT", "SELL", 118, 40m, "e2", "2"),     // the runaway shape: a sell nobody placed
            F(null, "ESNT", "SELL", 118, 39m, "e3", "3"),
        }, Now);

        Assert.Single(r.Strategies);
        var un = Assert.Single(r.Unattributed);
        Assert.Equal(PaperLedger.UnattributedKey, un.Key);
        Assert.Null(un.StrategyId);
        Assert.Equal(0m, un.RealisedLtd);                       // no buy lot → nothing realised
        Assert.Equal(236m, un.UnmatchedSellQty);                // reported as quantity instead
        Assert.Contains(un.Notes, n => n.Contains("no placement record"));
        Assert.Contains(un.Notes, n => n.Contains("no confirmed buy lot"));
    }

    [Fact]
    public void A_sell_against_an_assumed_buy_is_unmatched_not_profit()
    {
        // The +£5k phantom: a zero-cost reconcile BUY followed by a real SELL used to
        // book the full proceeds as profit. Here the buy is assumed, so the sell has
        // no confirmed lot and realises nothing.
        var r = PaperLedger.Build(new[]
        {
            F("ich", "META", "BUY", 10, 0m, "assumed_via_404:5", "5", Now.AddDays(-5)),
            F("ich", "META", "SELL", 10, 500m, "exec-z", "6", Now.AddDays(-1)),
        }, Now);
        var ich = Assert.Single(r.Strategies);
        Assert.Equal(0m, ich.RealisedLtd);
        Assert.Equal(10m, ich.UnmatchedSellQty);
        Assert.Equal(0, ich.ClosedTrades);
        Assert.Null(ich.WinRatePct);
    }

    [Fact]
    public void Currencies_and_brokers_are_never_blended()
    {
        var r = PaperLedger.Build(new[]
        {
            F("fx", "EURUSD", "BUY", 1, 1.10m, "d1", "1", broker: "IG_DEMO", ccy: "GBP"),
            F("fx", "EURUSD", "SELL", 1, 1.20m, "d2", "2", broker: "IG_DEMO", ccy: "GBP"),
            F("fx", "EURUSD", "BUY", 1, 1.10m, "e1", "3", broker: "IBKR_PAPER", ccy: "USD"),
            F("fx", "EURUSD", "SELL", 1, 1.00m, "e2", "4", broker: "IBKR_PAPER", ccy: "USD"),
        }, Now);
        Assert.Equal(2, r.Strategies.Count);
        var gbp = Assert.Single(r.Strategies, l => l.Currency == "GBP");
        var usd = Assert.Single(r.Strategies, l => l.Currency == "USD");
        Assert.Equal(0.10m, gbp.RealisedLtd);
        Assert.Equal(-0.10m, usd.RealisedLtd);
        Assert.Equal("IG_DEMO", gbp.Broker);
    }

    [Fact]
    public void Totals_and_ordering_most_recent_first()
    {
        var r = PaperLedger.Build(new[]
        {
            F("old", "A", "BUY", 1, 1m, "x", "1", Now.AddDays(-60)),
            F("new", "B", "BUY", 1, 1m, "y", "2", Now.AddDays(-1)),
            F("new", "B", "BUY", 1, 0m, "assumed_via_x", "2", Now.AddDays(-1)),
        }, Now);
        Assert.Equal(3, r.FillsSeen);
        Assert.Equal(2, r.ConfirmedFills);
        Assert.Equal(1, r.AssumedFills);
        Assert.Equal(new[] { "new", "old" }, r.Strategies.Select(l => l.Key).ToArray());
        var old = Assert.Single(r.Strategies, l => l.Key == "old");
        Assert.Equal(1, old.ConfirmedFills);
        Assert.DoesNotContain(old.Notes, n => n.Contains("nothing here is money"));
        var fresh = Assert.Single(r.Strategies, l => l.Key == "new");
        Assert.Contains(fresh.Notes, n => n.Contains("excluded from money"));
    }
}
