using TradePro.Api.Models;
using TradePro.Api.Providers;

namespace TradePro.Api.Simulation;

public interface ISimulator
{
    Task<SimulationResult> RunAsync(SimulationRequest request, CancellationToken ct);
}

public sealed class Simulator : ISimulator
{
    private readonly IMarketDataRegistry _providers;
    private readonly IStrategyRegistry _strategies;

    public Simulator(IMarketDataRegistry providers, IStrategyRegistry strategies)
    {
        _providers = providers;
        _strategies = strategies;
    }

    public async Task<SimulationResult> RunAsync(SimulationRequest req, CancellationToken ct)
    {
        var provider = _providers.Resolve(req.Provider);
        var strategy = _strategies.Resolve(req.Strategy);
        var fees = req.Fees ?? FeeModels.Uk;

        var series = await provider.GetCandlesAsync(req.Symbol, "1d", req.From, req.To, ct);
        var candles = series.Candles;
        if (candles.Count == 0)
        {
            return Empty(req);
        }

        var signals = strategy.Generate(candles, req.Params ?? new Dictionary<string, double>());
        var trailing = NormalisedStopPct(req.StopLoss?.TrailingPct);
        var fixedStop = NormalisedStopPct(req.StopLoss?.FixedPct);
        // ATR-multiplier stops scale with volatility instead of a fixed
        // percentage. Pre-compute the whole ATR(14) series once so the
        // bar loop just indexes into it — same Wilder math as the
        // Python comparator's market_state.atr_14.
        var trailingAtr = req.StopLoss?.TrailingAtrMultiple;
        var fixedAtr = req.StopLoss?.FixedAtrMultiple;
        var useAtr = (trailingAtr is > 0m) || (fixedAtr is > 0m);
        decimal?[] atrSeries = useAtr
            ? Indicators.Atr(
                candles.Select(c => c.High).ToArray(),
                candles.Select(c => c.Low).ToArray(),
                candles.Select(c => c.Close).ToArray(),
                14)
            : Array.Empty<decimal?>();

        decimal cash = req.InitialCapital;
        decimal qty = 0m;
        // Entry-anchored levels for the stop overlay. `entryPrice` is
        // set on the BUY fill; `highWaterMark` rolls up each bar while
        // we're in position so trailing stops lock in gains.
        // `entryAtr` is the ATR reading at the bar of entry — Fixed
        // ATR stops anchor to this so the stop level doesn't drift
        // with subsequent vol changes.
        decimal entryPrice = 0m;
        decimal highWaterMark = 0m;
        decimal entryAtr = 0m;
        int stopExits = 0;
        var trades = new List<Trade>();
        var equity = new List<EquityPoint>(candles.Count);

        for (var i = 0; i < candles.Count; i++)
        {
            var price = candles[i].AdjOrClose;

            // Update the trailing reference BEFORE evaluating stops so
            // a brand-new high on the same bar doesn't immediately trip
            // the trailing exit on its own pullback. We're effectively
            // measuring "are we below the highest close so far".
            if (qty > 0m && price > highWaterMark) highWaterMark = price;

            // Stop-loss evaluated FIRST each bar so a same-bar
            // strategy-exit signal can't pre-empt the risk overlay —
            // the whole point of the stop is to be the floor.
            //
            // Up to FOUR stops can fire on the same bar. Combine the
            // reason string so the trade log tells the truth about
            // which gates tripped (often >1 on a sharp move).
            if (qty > 0m && (trailing.HasValue || fixedStop.HasValue
                             || trailingAtr is > 0m || fixedAtr is > 0m))
            {
                // Current bar's ATR (null until the indicator's warmup
                // period elapses). Used by the trailing-ATR stop.
                decimal currentAtr = (useAtr && atrSeries.Length > i)
                    ? (atrSeries[i] ?? 0m) : 0m;

                bool trailingPctHit = trailing.HasValue
                    && highWaterMark > 0m
                    && price <= highWaterMark * (1m - trailing.Value);
                bool fixedPctHit = fixedStop.HasValue
                    && entryPrice > 0m
                    && price <= entryPrice * (1m - fixedStop.Value);
                bool trailingAtrHit = trailingAtr is > 0m
                    && currentAtr > 0m
                    && highWaterMark > 0m
                    && price <= highWaterMark - trailingAtr.Value * currentAtr;
                bool fixedAtrHit = fixedAtr is > 0m
                    && entryAtr > 0m
                    && entryPrice > 0m
                    && price <= entryPrice - fixedAtr.Value * entryAtr;

                if (trailingPctHit || fixedPctHit || trailingAtrHit || fixedAtrHit)
                {
                    var hits = new List<string>(4);
                    if (trailingPctHit) hits.Add("trailing");
                    if (fixedPctHit) hits.Add("fixed");
                    if (trailingAtrHit) hits.Add("trailing_atr");
                    if (fixedAtrHit) hits.Add("fixed_atr");
                    var reason = "stop_loss_" + string.Join("_", hits);
                    var proceeds = qty * price - fees.CommissionPerTrade;
                    cash += proceeds;
                    trades.Add(new Trade(candles[i].Timestamp, "SELL", price, qty, fees.CommissionPerTrade, reason));
                    qty = 0m;
                    entryPrice = 0m;
                    highWaterMark = 0m;
                    entryAtr = 0m;
                    stopExits++;
                    equity.Add(new EquityPoint(candles[i].Timestamp, cash, cash, 0m));
                    continue;
                }
            }

            if (signals[i] == Signal.Buy && qty == 0m && cash > 0m)
            {
                // effective cost per share includes stamp duty on notional + flat commission amortised
                var notional = cash - fees.CommissionPerTrade;
                if (notional <= 0m) continue;
                var effectivePrice = price * (1m + fees.StampDutyRate);
                var boughtQty = Math.Floor(notional / effectivePrice * 10000m) / 10000m;
                if (boughtQty <= 0m) continue;
                var stamp = boughtQty * price * fees.StampDutyRate;
                var totalFees = stamp + fees.CommissionPerTrade;
                cash -= boughtQty * price + totalFees;
                qty += boughtQty;
                entryPrice = price;
                highWaterMark = price;
                // Pin the ATR at entry so Fixed-ATR stops anchor to the
                // volatility on the day we entered, not later vol shifts.
                entryAtr = (useAtr && atrSeries.Length > i) ? (atrSeries[i] ?? 0m) : 0m;
                trades.Add(new Trade(candles[i].Timestamp, "BUY", price, boughtQty, totalFees, strategy.Name));
            }
            else if (signals[i] == Signal.Sell && qty > 0m)
            {
                var proceeds = qty * price - fees.CommissionPerTrade;
                cash += proceeds;
                trades.Add(new Trade(candles[i].Timestamp, "SELL", price, qty, fees.CommissionPerTrade, strategy.Name));
                qty = 0m;
                entryPrice = 0m;
                highWaterMark = 0m;
                entryAtr = 0m;
            }

            var mark = cash + qty * price;
            equity.Add(new EquityPoint(candles[i].Timestamp, mark, cash, qty * price));
        }

        // Close any open position at the last close so the PnL is realised.
        if (qty > 0m && candles.Count > 0)
        {
            var lastPrice = candles[^1].AdjOrClose;
            var proceeds = qty * lastPrice - fees.CommissionPerTrade;
            cash += proceeds;
            trades.Add(new Trade(candles[^1].Timestamp, "SELL", lastPrice, qty, fees.CommissionPerTrade, "close_at_end"));
            qty = 0m;
            if (equity.Count > 0)
                equity[^1] = equity[^1] with { Equity = cash, Cash = cash, Position = 0m };
        }

        var finalEquity = equity.Count > 0 ? equity[^1].Equity : req.InitialCapital;
        var years = Math.Max((candles[^1].Timestamp - candles[0].Timestamp).TotalDays / 365.25, 1.0 / 365.25);
        var totalReturn = (finalEquity - req.InitialCapital) / req.InitialCapital;
        var cagr = (decimal)(Math.Pow((double)(finalEquity / req.InitialCapital), 1.0 / years) - 1.0);

        return new SimulationResult(
            req.Symbol,
            strategy.Name,
            req.Currency,
            req.InitialCapital,
            finalEquity,
            totalReturn * 100m,
            cagr * 100m,
            MaxDrawdownPct(equity),
            Sharpe(equity),
            trades.Count,
            trades,
            equity,
            stopExits);
    }

    /// <summary>Normalise an inbound stop-loss percentage (UI-friendly
    /// 0-100) into a fractional decimal (e.g. 10 → 0.10). Returns null
    /// when the stop is unset or 0 so the bar loop can branch on a
    /// single .HasValue check.</summary>
    private static decimal? NormalisedStopPct(decimal? raw)
    {
        if (!raw.HasValue) return null;
        if (raw.Value <= 0m) return null;
        return raw.Value / 100m;
    }

    private static SimulationResult Empty(SimulationRequest req) => new(
        req.Symbol, req.Strategy, req.Currency, req.InitialCapital,
        req.InitialCapital, 0m, 0m, 0m, 0m, 0, Array.Empty<Trade>(), Array.Empty<EquityPoint>(), 0);

    private static decimal MaxDrawdownPct(IReadOnlyList<EquityPoint> curve)
    {
        if (curve.Count == 0) return 0m;
        decimal peak = curve[0].Equity;
        decimal worst = 0m;
        foreach (var p in curve)
        {
            if (p.Equity > peak) peak = p.Equity;
            if (peak > 0m)
            {
                var dd = (p.Equity - peak) / peak;
                if (dd < worst) worst = dd;
            }
        }
        return worst * 100m;
    }

    private static decimal Sharpe(IReadOnlyList<EquityPoint> curve)
    {
        if (curve.Count < 3) return 0m;
        var rets = new List<double>(curve.Count);
        for (var i = 1; i < curve.Count; i++)
        {
            var prev = (double)curve[i - 1].Equity;
            var cur = (double)curve[i].Equity;
            if (prev > 0) rets.Add((cur - prev) / prev);
        }
        if (rets.Count == 0) return 0m;
        var mean = rets.Average();
        var variance = rets.Sum(r => (r - mean) * (r - mean)) / rets.Count;
        var stdev = Math.Sqrt(variance);
        if (stdev == 0) return 0m;
        // Annualise assuming ~252 trading days.
        return (decimal)(mean / stdev * Math.Sqrt(252));
    }
}
