namespace TradePro.Api.Models;

public record HitRateRequest(
    string Symbol,
    string? Provider,
    string Strategy,
    int LookbackYears,
    Dictionary<string, double>? Params);

public record HitRateTrade(
    DateTime EntryDate,
    DateTime? ExitDate,
    decimal EntryPrice,
    decimal? ExitPrice,
    decimal? ReturnPct,
    int? HoldingDays,
    bool IsOpen);

public record HitRateResult(
    string Symbol,
    string Strategy,
    DateTime From,
    DateTime To,
    int TotalTrades,
    int Winners,
    int Losers,
    decimal WinRatePct,
    decimal AvgWinnerPct,
    decimal AvgLoserPct,
    decimal MedianHoldingDays,
    decimal BestPct,
    decimal WorstPct,
    decimal ExpectancyPct,         // average return per trade
    decimal TotalReturnPct,        // sum of per-trade returns (rough, ignores compounding)
    IReadOnlyList<HitRateTrade> Trades);
