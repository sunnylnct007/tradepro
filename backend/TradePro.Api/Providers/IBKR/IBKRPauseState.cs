namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Runtime pause switch for ALL of TradePro's IBKR usage (bar harvester, daily
/// backfill, account-state push). IBKR allows only ONE active Web-API session per
/// account, so while TradePro holds + tickles the session the user CANNOT log into
/// the IBKR Client Portal for that same paper account. Pausing lets the user take
/// the session back (e.g. to reset the paper account / reconcile positions).
///
/// On PAUSE the pause endpoint also calls IBKRClient.LogoutAsync() to actively
/// release the brokerage session; the harvester/backfill then skip all IBKR calls
/// so nothing re-auths behind the user's back. RESUME clears the flag and the next
/// tick re-establishes the session. In-memory only (a pause is operator intent for
/// "right now", not a durable config) — an API restart clears it, harvesting resumes.
/// </summary>
public sealed class IBKRPauseState
{
    private volatile bool _paused;

    public bool Paused => _paused;
    public DateTime? PausedAtUtc { get; private set; }
    public string? Reason { get; private set; }

    public void Pause(string? reason)
    {
        _paused = true;
        PausedAtUtc = DateTime.UtcNow;
        Reason = string.IsNullOrWhiteSpace(reason) ? "operator paused (free the IBKR portal)" : reason;
    }

    public void Resume()
    {
        _paused = false;
        PausedAtUtc = null;
        Reason = null;
    }
}
