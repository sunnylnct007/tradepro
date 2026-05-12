namespace TradePro.Api.Models;

public record FeeModel(
    decimal CommissionPerTrade,   // flat fee per trade in account currency
    decimal StampDutyRate,        // e.g. 0.005 for UK 0.5% on buy
    decimal FxSpread              // e.g. 0.005 for 0.5% when converting currency
);

public static class FeeModels
{
    /// UK execution-only broker defaults. 0.5% stamp duty applies to most LSE
    /// main-market shares on buy (AIM shares are exempt — override if needed).
    public static readonly FeeModel Uk = new(
        CommissionPerTrade: 0m,
        StampDutyRate: 0.005m,
        FxSpread: 0m);

    public static readonly FeeModel Zero = new(0m, 0m, 0m);
}

public record SimulationRequest(
    string Symbol,
    string? Provider,
    string Strategy,                // "buy_and_hold" | "sma_crossover"
    DateTime From,
    DateTime To,
    decimal InitialCapital,
    string Currency,                // "GBP", "USD", ...
    FeeModel? Fees,
    Dictionary<string, double>? Params,
    StopLossConfig? StopLoss = null);

/// <summary>
/// Optional risk overlay applied on top of the strategy's own exit
/// signals. Both stops can be set at once — whichever triggers first
/// closes the position. Either field at null OR 0 means that stop is
/// disabled.
///
///   TrailingPct: 10 ⇒ exit when price drops 10% below the highest
///     close seen since entry. Locks in gains as the trade moves up.
///   FixedPct:    10 ⇒ exit when price drops 10% below the entry
///     price. Does not move as the trade progresses.
/// </summary>
public record StopLossConfig(
    decimal? TrailingPct,
    decimal? FixedPct);

public record Trade(
    DateTime Timestamp,
    string Side,                    // "BUY" or "SELL"
    decimal Price,
    decimal Quantity,
    decimal Fees,
    string Reason);

public record EquityPoint(DateTime Timestamp, decimal Equity, decimal Cash, decimal Position);

public record SimulationResult(
    string Symbol,
    string Strategy,
    string Currency,
    decimal InitialCapital,
    decimal FinalEquity,
    decimal TotalReturnPct,
    decimal CagrPct,
    decimal MaxDrawdownPct,
    decimal SharpeRatio,
    int TradeCount,
    IReadOnlyList<Trade> Trades,
    IReadOnlyList<EquityPoint> EquityCurve,
    /// <summary>How many of the SELL trades came from a stop-loss
    /// (trailing or fixed) rather than the strategy's own exit signal.
    /// Lets the UI show "stops fired N of M times" so the user can
    /// see whether the overlay is actively shaping the outcome or
    /// just sitting unused.</summary>
    int StopLossExits = 0);
