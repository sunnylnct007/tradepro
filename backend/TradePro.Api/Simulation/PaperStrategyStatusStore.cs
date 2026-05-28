namespace TradePro.Api.Simulation;

/// <summary>
/// Runtime overrides for a strategy's promotion-lifecycle status.
/// The code default (Strategy.status Python ClassVar) is shipped with
/// the strategy catalog; this store carries operator-applied overrides
/// that shadow the default on read. Storage layer is per-strategy_id
/// upsert (see migration 008).
/// </summary>
public interface IPaperStrategyStatusStore
{
    /// <summary>Returns the override status for `strategyId`, or null
    /// when no override is set (caller falls back to the catalog
    /// default). Caller-side merge keeps the contract simple.</summary>
    StrategyStatusOverride? Get(string strategyId);

    /// <summary>Returns every override row. Lets the read endpoint
    /// hand the frontend a single dict and the merge happens in one
    /// place instead of N round-trips.</summary>
    IReadOnlyList<StrategyStatusOverride> ListAll();

    /// <summary>UPSERT the override. `updatedBy` is the principal that
    /// applied the change; surfaces in the audit trail. Status values
    /// must be in the allowed enum (CHECK constraint enforces).</summary>
    StrategyStatusOverride Upsert(string strategyId, string status, string updatedBy);

    /// <summary>Drop the override row so the catalog default wins again.
    /// Returns true when a row was deleted, false when none existed.</summary>
    bool Clear(string strategyId);
}

/// <summary>One override row — strategyId is the key, status carries
/// the runtime value, updatedAt + updatedBy form the audit trail.</summary>
public sealed record StrategyStatusOverride(
    string StrategyId,
    string Status,
    DateTime UpdatedAtUtc,
    string UpdatedBy
);
