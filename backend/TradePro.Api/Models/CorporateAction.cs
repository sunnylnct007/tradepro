namespace TradePro.Api.Models;

/// <summary>
/// One corporate action (dividend payment or stock split).
/// Rendered as "D" / "S" chips on PriceHistoryChart at the event date.
/// </summary>
/// <param name="Date">YYYY-MM-DD of the event.</param>
/// <param name="Type">"dividend" or "split".</param>
/// <param name="Amount">Cash amount per share for dividend events (null for splits).</param>
/// <param name="Ratio">Human-readable ratio for split events, e.g. "4:1" (null for dividends).</param>
public record CorporateActionDto(
    string Date,
    string Type,
    double? Amount,
    string? Ratio);
