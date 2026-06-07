namespace TradePro.Api.Providers.IG;

/// <summary>
/// Phase P2.1 — singleton holder for the IG harvester's runtime
/// state. The BackgroundService writes here on every tick; the
/// cockpit status endpoint reads from here.
///
/// Keeping it in-process (rather than a DB heartbeat row) means:
///   * Zero DB churn per tick — important when the harvester runs
///     forever at 5-min cadence.
///   * The cockpit can show "last tick 23 seconds ago" with single-
///     digit ms latency — the support team experience needs to be
///     responsive.
///   * If the API restarts, the holder resets to "never ticked";
///     the cockpit shows that honestly until the next tick lands.
///
/// Locks aren't needed — every field is either atomic (DateTime,
/// int) or string-assignment. Last-write-wins is fine because the
/// harvester is single-threaded within a tick.
/// </summary>
public sealed class IGHarvesterStatus
{
    /// <summary>Whether the harvester is enabled (per config).
    /// When false, the BackgroundService logs once and returns.</summary>
    public bool Enabled { get; set; }

    /// <summary>When the harvester last completed a tick (success
    /// or partial). Null = never ticked since process start.</summary>
    public DateTime? LastTickAtUtc { get; set; }

    /// <summary>When the next tick is roughly due. Lets the cockpit
    /// show "next tick in 4m32s" — operational feedback the support
    /// team needs to tell if the harvester is alive vs frozen.</summary>
    public DateTime? NextTickEtaUtc { get; set; }

    /// <summary>Captured + failed counts on the most recent tick.
    /// Both reset to zero on the next tick — these are last-tick
    /// counters, not cumulative.</summary>
    public int LastTickCaptured { get; set; }
    public int LastTickFailed { get; set; }

    /// <summary>Total number of configured epics. Lets the cockpit
    /// show "ticked 47 of 50 epics" so the support team sees if
    /// some are silently broken.</summary>
    public int ConfiguredEpicCount { get; set; }

    /// <summary>How long the configured interval is, in seconds.
    /// Persisted on the holder so the cockpit can render "every 5
    /// min" without re-reading IConfiguration.</summary>
    public int IntervalSeconds { get; set; }

    /// <summary>Last error from a tick that threw. Cleared on next
    /// successful tick. Lets support eyeball recurring failures.</summary>
    public string? LastError { get; set; }

    /// <summary>When the harvester process initialised. Combined with
    /// LastTickAtUtc this tells the cockpit "started 2 hours ago,
    /// last ticked 4 min ago" — the support team's "is this thing
    /// alive" question answers in one glance.</summary>
    public DateTime StartedAtUtc { get; } = DateTime.UtcNow;
}
