namespace TradePro.Api.Models;

/// How long a typical trade in this strategy stays open.
/// These are approximate — actual holding periods depend on parameters
/// and market conditions, but the label tells a user what kind of
/// trading behaviour to expect.
public enum TimeHorizon
{
    Intraday,    // minutes to hours, flat by close
    Short,       // days to a couple of weeks
    Mid,         // a few weeks to a few months
    Long,        // months to years
    Any,         // parameter-dependent; no fixed horizon
}

public record StrategyMetadata(
    string Name,                   // stable id — matches the strategy registry key
    string DisplayName,            // human-readable (e.g. "SMA crossover")
    string OneLiner,               // single-sentence "what it does"
    string BestIn,                 // market condition where it shines
    string WorstIn,                // market condition where it fails
    TimeHorizon Horizon,           // typical holding period
    string HorizonText,            // e.g. "mid-term (weeks to months)"
    Dictionary<string, double>? DefaultParams,
    string[]? ParamKeys
);
