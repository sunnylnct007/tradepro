using System.Net;
using System.Text.Json;
using Dapper;
using Npgsql;
using TradePro.Api.Data.Stores;
using TradePro.Api.Providers.Trading212;

namespace TradePro.Api.Positions;

/// <summary>
/// Step 3 — Trade plan. Derives the trade list needed to move from
/// current broker positions to the algo's recommended target portfolio.
///
/// Inputs:
///   - Latest strategy_runs + strategy_decisions for the strategy
///     (step 1's output — "what the algo wants to hold")
///   - Cached T212 positions (broker-as-golden, from step 2)
///   - Current portfolio cash (for converting target_weight → dollars)
///
/// Output:
///   - List of TradeIntent: { symbol, side, qty, target_notional,
///     current_notional, reason, risk_class (when wired) }
///
/// Today-only: derives from the latest live-portfolio run. History +
/// approval audit live in oms_orders once orders are dispatched —
/// no separate trade_plans table for now (KISS; only add if we need
/// "what was the plan when I clicked approve" audit, which oms_orders
/// + decision_log already cover indirectly).
/// </summary>
public sealed class TradePlanService
{
    private readonly Trading212DemoPositionsCache _t212Positions;
    private readonly Trading212DemoCashCache _t212Cash;
    private readonly NpgsqlDataSource _db;
    private readonly ILogger<TradePlanService> _log;

    // Don't bother emitting trades smaller than this — broker fees +
    // slippage make sub-$50 rebalances noise rather than alpha.
    // Tunable via Settings KV later.
    private const decimal MinTradeNotionalUsd = 50m;

    public TradePlanService(
        Trading212DemoPositionsCache t212Positions,
        Trading212DemoCashCache t212Cash,
        NpgsqlDataSource db,
        ILogger<TradePlanService> log)
    {
        _t212Positions = t212Positions;
        _t212Cash = t212Cash;
        _db = db;
        _log = log;
    }

    public async Task<TradePlanResult> BuildAsync(
        string strategy, CancellationToken ct)
    {
        await using var conn = await _db.OpenConnectionAsync();

        // 1. Latest live-portfolio run (slow-loop output).
        var head = await conn.QueryFirstOrDefaultAsync<RunHeader>(@"
            SELECT run_id AS RunId, strategy AS Strategy,
                   as_of_utc AS AsOfUtc, regime_state AS RegimeState,
                   summary::text AS SummaryText
            FROM strategy_runs
            WHERE strategy = @strategy
            ORDER BY as_of_utc DESC LIMIT 1;", new { strategy });
        if (head is null)
        {
            return TradePlanResult.NoPlan(strategy,
                "no live-portfolio run yet — run `tradepro-live-portfolio --push`");
        }
        var decisions = (await conn.QueryAsync<DecisionRow>(@"
            SELECT sleeve, symbol,
                   target_weight AS TargetWeight,
                   signal,
                   regime_pass AS RegimePass,
                   vol,
                   risk_class AS RiskClass,
                   detail::text AS DetailText
            FROM strategy_decisions
            WHERE run_id = @runId;",
            new { runId = head.RunId })).ToList();

        // 2. Current broker positions (T212 cached) + cash.
        var posResult = await _t212Positions.GetAsync(ct);
        var cashResult = await _t212Cash.GetAsync(ct);
        if (posResult.HttpStatus == (int)HttpStatusCode.TooManyRequests
            && (posResult.Positions is null || posResult.Positions.Count == 0))
        {
            return TradePlanResult.NoPlan(strategy,
                "T212 rate-limited with no cached positions — try again in 30s");
        }
        var portfolioValueUsd = (cashResult.Total ?? 0m);
        if (portfolioValueUsd <= 0m)
        {
            // Use Free + Invested as a fallback — Total may be null
            // until T212 returns a complete response.
            portfolioValueUsd = (cashResult.Free ?? 0m) + (cashResult.Invested ?? 0m);
        }
        if (portfolioValueUsd <= 0m)
        {
            return TradePlanResult.NoPlan(strategy,
                "could not derive portfolio value from T212 — cash endpoint returned no totals");
        }
        var currentByTicker = (posResult.Positions ?? new List<Trading212Position>())
            .GroupBy(p => NormaliseTicker(p.Ticker))
            .ToDictionary(g => g.Key, g => new CurrentPos(
                Qty: g.Sum(x => x.Quantity),
                AvgPrice: g.First().AveragePricePaid ?? 0m,
                CurrentPrice: g.First().CurrentPrice ?? g.First().AveragePricePaid ?? 0m));

        // 3. Per-target: compute desired notional → trade.
        var intents = new List<TradeIntent>();
        var seenTickers = new HashSet<string>();
        foreach (var d in decisions.OrderByDescending(x => x.TargetWeight))
        {
            var ticker = NormaliseTicker(d.Symbol);
            seenTickers.Add(ticker);
            var targetNotional = portfolioValueUsd * (decimal)d.TargetWeight;
            var current = currentByTicker.TryGetValue(ticker, out var c) ? c : null;
            var currentNotional = current is null
                ? 0m
                : current.Qty * current.CurrentPrice;
            var diff = targetNotional - currentNotional;

            if (Math.Abs(diff) < MinTradeNotionalUsd) continue;

            // Use current price for sizing — if we don't have one
            // (no existing position to crib from), skip and let the
            // operator approve manually with their own price.
            var price = current?.CurrentPrice ?? 0m;
            if (price <= 0m)
            {
                // Don't emit a trade we can't size. The decision is
                // still surfaced in the plan with a `priceUnavailable`
                // flag so the operator sees it.
                intents.Add(new TradeIntent(
                    Sleeve: d.Sleeve, Symbol: d.Symbol,
                    Side: diff > 0 ? "BUY" : "SELL",
                    Qty: 0m, TargetNotional: targetNotional,
                    CurrentNotional: currentNotional, DiffNotional: diff,
                    Price: 0m, RiskClass: d.RiskClass,
                    Reason: "price unavailable from broker — operator must approve manually",
                    PriceUnavailable: true));
                continue;
            }

            var qty = Math.Round(Math.Abs(diff) / price, 4);
            if (qty <= 0m) continue;
            var side = diff > 0 ? "BUY" : "SELL";
            var reason = BuildReason(d, current, diff);
            intents.Add(new TradeIntent(
                Sleeve: d.Sleeve, Symbol: d.Symbol,
                Side: side, Qty: qty,
                TargetNotional: targetNotional,
                CurrentNotional: currentNotional,
                DiffNotional: diff, Price: price,
                RiskClass: d.RiskClass, Reason: reason,
                PriceUnavailable: false));
        }

        // 4. Exits — positions we currently hold that the algo
        //    explicitly wants out of (target_weight = 0 OR signal = 0
        //    AND the symbol is in the algo's universe). Don't touch
        //    positions OUTSIDE the algo's universe (those may be
        //    held for reasons the algo doesn't know about).
        var algoTickers = decisions.Select(d => NormaliseTicker(d.Symbol)).ToHashSet();
        foreach (var (ticker, pos) in currentByTicker)
        {
            if (!algoTickers.Contains(ticker)) continue; // not algo-managed
            if (seenTickers.Contains(ticker)) continue;  // already handled
            var notional = pos.Qty * pos.CurrentPrice;
            if (notional < MinTradeNotionalUsd) continue;
            // Algo decided this symbol is target-weight 0 (flat) but
            // we still hold it → exit.
            intents.Add(new TradeIntent(
                Sleeve: "auto-exit",
                Symbol: ticker,
                Side: "SELL", Qty: pos.Qty,
                TargetNotional: 0m, CurrentNotional: notional,
                DiffNotional: -notional, Price: pos.CurrentPrice,
                RiskClass: null,
                Reason: "algo target-weight=0 — exit existing position",
                PriceUnavailable: false));
        }

        return new TradePlanResult(
            Strategy: strategy,
            RunId: head.RunId,
            AsOfUtc: head.AsOfUtc,
            RegimeState: head.RegimeState,
            PortfolioValueUsd: portfolioValueUsd,
            Intents: intents,
            Skipped: decisions.Count - intents.Count,
            NoPlanReason: null);
    }

    private static string BuildReason(DecisionRow d, CurrentPos? current, decimal diff)
    {
        JsonElement? detail = null;
        if (!string.IsNullOrEmpty(d.DetailText))
        {
            detail = JsonbHelpers.FromJsonb(d.DetailText);
        }
        var cloudPos = detail.HasValue && detail.Value.TryGetProperty("cloud_position", out var cp)
            ? cp.GetString() : null;
        var direction = diff > 0 ? "open/add" : "trim";
        var hasCurrent = current is { Qty: > 0m };
        var actionLabel = hasCurrent
            ? (diff > 0 ? "add to existing" : "trim back")
            : (diff > 0 ? "new position" : "exit");
        var sig = $"signal={d.Signal:0}";
        var regime = d.RegimePass ? "regime-ok" : "regime-blocked";
        var cloud = cloudPos is null ? "" : $" · cloud={cloudPos}";
        return $"{actionLabel} ({direction} {Math.Abs(diff):C0}); {sig} · {regime}{cloud}";
    }

    private static string NormaliseTicker(string ticker)
    {
        if (string.IsNullOrWhiteSpace(ticker)) return string.Empty;
        var t = ticker.Trim().ToUpperInvariant();
        var underscore = t.IndexOf('_');
        return underscore > 0 ? t[..underscore] : t;
    }

    private sealed record RunHeader(
        Guid RunId, string Strategy, DateTime AsOfUtc,
        string? RegimeState, string? SummaryText);

    private sealed record DecisionRow(
        string Sleeve, string Symbol, double TargetWeight, double Signal,
        bool RegimePass, double? Vol, string? RiskClass, string? DetailText);

    private sealed record CurrentPos(
        decimal Qty, decimal AvgPrice, decimal CurrentPrice);
}

// ─── Public result records ──────────────────────────────────────────────

public sealed record TradeIntent(
    string Sleeve,
    string Symbol,
    string Side,
    decimal Qty,
    decimal TargetNotional,
    decimal CurrentNotional,
    decimal DiffNotional,
    decimal Price,
    string? RiskClass,
    string Reason,
    bool PriceUnavailable);

public sealed record TradePlanResult(
    string Strategy,
    Guid? RunId,
    DateTime? AsOfUtc,
    string? RegimeState,
    decimal PortfolioValueUsd,
    IReadOnlyList<TradeIntent> Intents,
    int Skipped,
    string? NoPlanReason)
{
    public static TradePlanResult NoPlan(string strategy, string reason) =>
        new(strategy, null, null, null, 0m,
            Array.Empty<TradeIntent>(), 0, reason);

    public bool HasPlan => NoPlanReason is null;
}
