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
    Dictionary<string, double>? Params);

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
    IReadOnlyList<EquityPoint> EquityCurve);
