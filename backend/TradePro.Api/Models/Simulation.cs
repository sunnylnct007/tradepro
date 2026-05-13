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
/// signals. All four stop variants can be set at once — whichever
/// triggers first closes the position. Any field at null OR 0 means
/// that stop is disabled.
///
/// Percentage stops (TrailingPct / FixedPct) are fixed-width and
/// don't adapt to volatility — a 5% stop on USMV makes sense, the
/// same 5% on NG=F (gas futures, often 8% daily range) gets stopped
/// out by noise.
///
/// ATR-multiple stops (TrailingAtrMultiple / FixedAtrMultiple) scale
/// the stop width with the symbol's own volatility — "stop = 2× ATR
/// below highest close since entry" widens for vol-y instruments
/// and tightens for calm ones. ATR(14) is computed inside the
/// simulator using the same Wilder math as the comparator's
/// market_state.atr_14.
///
///   TrailingPct: 10           ⇒ exit when price drops 10% below the
///                                highest close seen since entry.
///   FixedPct:    10           ⇒ exit when price drops 10% below the
///                                entry price.
///   TrailingAtrMultiple: 2.0  ⇒ exit when price drops 2× ATR below
///                                the highest close since entry.
///   FixedAtrMultiple:    2.0  ⇒ exit when price drops 2× ATR (at
///                                entry) below the entry price.
/// </summary>
public record StopLossConfig(
    decimal? TrailingPct,
    decimal? FixedPct,
    decimal? TrailingAtrMultiple = null,
    decimal? FixedAtrMultiple = null);

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
