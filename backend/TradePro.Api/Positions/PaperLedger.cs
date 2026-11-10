namespace TradePro.Api.Positions;

/// <summary>
/// The paper P&amp;L ledger — ONE answer to "is this strategy making money?",
/// built only from what the broker confirmed, attributed only by the
/// placement record that caused it.
///
/// Owner, 9 Nov 2026: "the paper trading execution and reconciliation is still
/// a mess. not a clean way of saying if a strategy is making money or not."
/// Ruling, 10 Nov: "paper P&amp;L ledger = broker-confirmed fills by placement
/// record; unattributed as its own line."
///
/// WHY THE EXISTING /pnl/by-strategy COULD NOT ANSWER. It replays every fill
/// in the OMS, and the OMS holds two kinds: fills the BROKER reported (an
/// execution id, or its blotter status with a real average price) and fills
/// WE WROTE to make the OMS agree with a broker position we could not explain
/// (`assumed_via_position_reconcile:*`, `standing_reconcile*`, `reconcile-*`,
/// `purge-*`, price 0). On 10 Nov 2026, 68 of 357 filled orders were the
/// second kind. The old endpoint — correctly — refused to compute realised for
/// any strategy that had even one, so every IBKR sleeve read "realised NOT
/// computable" and the question went unanswered for months.
///
/// This ledger splits instead of refusing:
///   * MONEY  = FIFO realised over BROKER-CONFIRMED fills only, per symbol,
///              per (strategy, broker, currency). Currencies are never blended.
///   * ASSUMED = the reconcile-written fills, counted and sized on the same
///              line but NEVER in the money. They say "the OMS was corrected
///              here", which is a data fact about us, not a P&amp;L fact.
///   * UNATTRIBUTED = fills whose order carries NO strategy_id (manual, or a
///              reconcile adjustment with no owner) — its own line, never
///              folded into a strategy. The 29 Sep phantom shorts and the
///              1 Oct runaway both live here, not in a sleeve's number.
///
/// Pure and deterministic: the endpoint fetches rows, this class does the
/// arithmetic, the tests exercise this class. An unmatched SELL (a sell with
/// no prior confirmed BUY lot) is REPORTED as quantity, never realised against
/// a zero-cost basis — a fabricated basis is how a +£5k phantom was produced
/// once before.
/// </summary>
public static class PaperLedger
{
    public const string UnattributedKey = "unattributed";

    /// <summary>One OMS fill joined to its order. <paramref name="StrategyId"/>
    /// null = no placement record claims it.</summary>
    public readonly record struct FillRow(
        string? StrategyId, string Broker, string Symbol, string Side,
        decimal Qty, decimal Price, string Currency,
        string? BrokerFillId, string? BrokerOrderId, DateTime FillAtUtc);

    public enum Origin { BrokerConfirmed, Assumed }

    /// <summary>Prefixes the reconcilers stamp on fills they INVENT to make the
    /// OMS agree with the broker. Grep `RecordFillAsync(` for the writers; a new
    /// writer with a new prefix must be added here or its fills count as money.
    /// </summary>
    public static readonly string[] AssumedPrefixes =
    {
        "assumed_via_",        // GoldenSourceReconciler / OmsFillPoller 404 path
        "standing_reconcile",  // standing position reconcile
        "reconcile-",          // POST /oms/positions/sync-from-broker
        "purge-",              // paper-account reset purge
    };

    /// <summary>Broker-confirmed means: the broker gave us a price AND an
    /// identity for it — an execution id, or its own blotter status
    /// (`recon:{brokerOrderId}` carries the broker's average price). Anything
    /// we wrote ourselves, anything at price 0, and anything with no broker
    /// order id at all is ASSUMED. A PAPER (simulated, no broker) fill is
    /// therefore assumed too: nobody outside this process confirmed it.</summary>
    public static Origin Classify(string? brokerFillId, string? brokerOrderId, decimal price)
    {
        if (price <= 0m) return Origin.Assumed;
        if (string.IsNullOrWhiteSpace(brokerOrderId)) return Origin.Assumed;
        if (string.IsNullOrWhiteSpace(brokerFillId)) return Origin.Assumed;
        foreach (var p in AssumedPrefixes)
            if (brokerFillId.StartsWith(p, StringComparison.OrdinalIgnoreCase))
                return Origin.Assumed;
        return Origin.BrokerConfirmed;
    }

    /// <summary>Brokers whose fill rows cannot be turned into money by
    /// price × qty: IG CFDs carry a point value per contract that the OMS
    /// never stored, so FIFO over (price − price) × qty understates by orders
    /// of magnitude (the first live readout showed −£0.47 over 36 FX trades).
    /// Their realised comes from the broker's own closed-deal history
    /// (/api/integrations/ig/history), and this ledger says so instead of
    /// printing a wrong number.</summary>
    public static readonly string[] NoFifoBrokerPrefixes = { "IG" };

    public static bool FifoIsMoney(string broker) =>
        !NoFifoBrokerPrefixes.Any(p => (broker ?? "").StartsWith(p, StringComparison.OrdinalIgnoreCase));

    public sealed record Line(
        string Key,                 // strategy_id, or "unattributed"
        string? StrategyId,
        string Broker,
        string Currency,
        bool MoneyComputable,       // false → RealisedLtd/Today are null, see Notes
        decimal? RealisedLtd,       // money — confirmed fills only
        decimal? RealisedToday,
        int ClosedTrades,
        int WinningTrades,
        double? WinRatePct,
        decimal UnmatchedSellQty,   // sells with no confirmed buy lot — reported, not realised
        int ConfirmedFills,
        int AssumedFills,           // reconcile-written — counted, never money
        decimal AssumedQty,
        int Symbols,
        DateTime? FirstFillAtUtc,
        DateTime? LastFillAtUtc,
        IReadOnlyList<string> Notes);

    public sealed record Result(
        IReadOnlyList<Line> Strategies,
        IReadOnlyList<Line> Unattributed,
        int FillsSeen,
        int ConfirmedFills,
        int AssumedFills,
        string Basis);

    public const string BasisText =
        "Money = FIFO realised over BROKER-CONFIRMED fills only (execution id or broker "
        + "blotter price), per symbol, per strategy, per broker, per currency — never blended. "
        + "Fills the reconcilers wrote to make the OMS agree with the broker are counted as "
        + "ASSUMED and never enter the money. Orders with no strategy_id are UNATTRIBUTED, "
        + "their own line. A sell with no confirmed buy lot is reported as unmatched quantity, "
        + "not realised against a zero basis. IG CFD sleeves show no FIFO money (no point value "
        + "on the fill rows) — their realised is the broker's closed-deal history. Open (unrealised) "
        + "P&L is deliberately absent: "
        + "it is a mark, not a result.";

    public static Result Build(IEnumerable<FillRow> fills, DateTime utcNow)
    {
        var today = utcNow.Date;
        // (key, broker, currency) → symbol → ordered confirmed fills; plus assumed tallies.
        var groups = new Dictionary<(string Key, string Broker, string Ccy), Group>();
        int seen = 0, confirmed = 0, assumed = 0;

        foreach (var f in fills.OrderBy(f => f.FillAtUtc))
        {
            seen++;
            var key = string.IsNullOrWhiteSpace(f.StrategyId) ? UnattributedKey : f.StrategyId!;
            var gk = (key, f.Broker ?? "", string.IsNullOrWhiteSpace(f.Currency) ? "USD" : f.Currency);
            if (!groups.TryGetValue(gk, out var g))
                groups[gk] = g = new Group(key, string.IsNullOrWhiteSpace(f.StrategyId) ? null : f.StrategyId, gk.Item2, gk.Item3);

            g.First = g.First is null || f.FillAtUtc < g.First ? f.FillAtUtc : g.First;
            g.Last = g.Last is null || f.FillAtUtc > g.Last ? f.FillAtUtc : g.Last;
            g.SymbolsSeen.Add(f.Symbol ?? "");

            if (Classify(f.BrokerFillId, f.BrokerOrderId, f.Price) == Origin.Assumed)
            {
                assumed++;
                g.AssumedFills++;
                g.AssumedQty += Math.Abs(f.Qty);
                continue;
            }
            confirmed++;
            g.ConfirmedFills++;
            if (!g.Streams.TryGetValue(f.Symbol ?? "", out var stream))
                g.Streams[f.Symbol ?? ""] = stream = new List<PnlByStrategy.DatedFill>();
            stream.Add(new PnlByStrategy.DatedFill(
                f.Side, Math.Abs(f.Qty), f.Price,
                DateTime.SpecifyKind(f.FillAtUtc, DateTimeKind.Utc).Date == today));
        }

        var strategies = new List<Line>();
        var unattributed = new List<Line>();
        foreach (var g in groups.Values)
        {
            decimal ltd = 0m, tod = 0m, unmatched = 0m;
            int closed = 0, won = 0;
            foreach (var stream in g.Streams.Values)
            {
                var r = PnlByStrategy.FifoRealisedSplit(stream);
                ltd += r.RealisedLtd; tod += r.RealisedToday;
                closed += r.ClosedTrades; won += r.WinningTrades;
                unmatched += r.UnmatchedSellQty;
            }
            var notes = new List<string>();
            if (g.ConfirmedFills == 0)
                notes.Add("no broker-confirmed fills — nothing here is money");
            if (g.AssumedFills > 0)
                notes.Add($"{g.AssumedFills} fill(s) ({g.AssumedQty:0.##} units) were written by a reconciler to match the broker, not reported by it — counted, excluded from money");
            if (unmatched > 0m)
                notes.Add($"{unmatched:0.##} units sold with no confirmed buy lot — reported, not realised (short, or a buy the broker never confirmed)");
            if (g.Key == UnattributedKey)
                notes.Add("no placement record claims these fills (manual order, or a reconcile adjustment with no owner) — never folded into a strategy");

            var computable = FifoIsMoney(g.Broker);
            if (!computable)
                notes.Insert(0, $"{g.Broker}: fills carry no contract point value, so price × qty is not money — realised for this sleeve comes from the broker's closed-deal history (/api/integrations/ig/history); fill counts here are still real");
            var line = new Line(
                g.Key, g.StrategyId, g.Broker, g.Currency, computable,
                computable ? ltd : null, computable ? tod : null, closed, won,
                computable && closed > 0 ? 100.0 * won / closed : null,
                unmatched, g.ConfirmedFills, g.AssumedFills, g.AssumedQty,
                g.SymbolsSeen.Count, g.First, g.Last, notes);
            (g.Key == UnattributedKey ? unattributed : strategies).Add(line);
        }

        // Most recently active first — the sleeves that traded this week above
        // the retired ones, within each the broker alphabetical for stability.
        static IOrderedEnumerable<Line> Order(IEnumerable<Line> xs) =>
            xs.OrderByDescending(l => l.LastFillAtUtc ?? DateTime.MinValue).ThenBy(l => l.Key).ThenBy(l => l.Broker);

        return new Result(Order(strategies).ToList(), Order(unattributed).ToList(),
                          seen, confirmed, assumed, BasisText);
    }

    private sealed class Group
    {
        public Group(string key, string? strategyId, string broker, string currency)
        { Key = key; StrategyId = strategyId; Broker = broker; Currency = currency; }
        public string Key { get; }
        public string? StrategyId { get; }
        public string Broker { get; }
        public string Currency { get; }
        public Dictionary<string, List<PnlByStrategy.DatedFill>> Streams { get; } = new(StringComparer.OrdinalIgnoreCase);
        public HashSet<string> SymbolsSeen { get; } = new(StringComparer.OrdinalIgnoreCase);
        public int ConfirmedFills, AssumedFills;
        public decimal AssumedQty;
        public DateTime? First, Last;
    }
}
