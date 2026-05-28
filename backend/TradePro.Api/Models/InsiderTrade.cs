namespace TradePro.Api.Models;

/// <summary>
/// One insider buy transaction. Only purchase transactions are surfaced —
/// sales are ambiguous (tax planning, 10b5-1 auto-sell plans, diversification)
/// and carry far less predictive signal than discretionary buys.
/// Rendered as a green "I" chip on PriceHistoryChart at the trade date.
/// </summary>
/// <param name="Date">YYYY-MM-DD of the transaction.</param>
/// <param name="Name">Insider's name.</param>
/// <param name="Title">Role at the company, e.g. "Director", "CEO".</param>
/// <param name="Shares">Number of shares purchased.</param>
/// <param name="Value">Approximate dollar value of the purchase (shares × price).</param>
public record InsiderTradeDto(
    string Date,
    string? Name,
    string? Title,
    long? Shares,
    double? Value);
