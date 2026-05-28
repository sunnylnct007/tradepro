namespace TradePro.Api.Models;

/// <summary>
/// One historical earnings announcement used to paint a vertical reference
/// line on PriceHistoryChart. Coloured by beat/miss (green/red/grey).
/// </summary>
/// <param name="Date">YYYY-MM-DD of the announcement.</param>
/// <param name="EpsActual">Reported EPS (null if not yet reported).</param>
/// <param name="EpsEstimate">Consensus EPS estimate at the time of the report.</param>
/// <param name="SurprisePct">
/// Beat/miss as a percentage, e.g. +5.2 = beat by 5.2%, -3.1 = missed by 3.1%.
/// Already in percentage units — matches the Python layer's Surprise(%) column.
/// </param>
public record EarningsMarkerDto(
    string Date,
    double? EpsActual,
    double? EpsEstimate,
    double? SurprisePct);
