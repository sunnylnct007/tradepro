using System.Security.Claims;

namespace TradePro.Api.Auth;

/// <summary>
/// Gates LIVE broker data (real-money accounts) to an allow-listed set of
/// Firebase users. Demo/paper data is visible to every signed-in user; LIVE
/// rows (T212 LIVE, IG live, IBKR LIVE) are hidden unless the caller is on the
/// "Firebase:LiveDataUserIds" allow-list — so an admin signed in for support
/// sees demo only, while the trader (allow-listed) sees both.
///
/// Behaviour:
///   • Ingest-token caller (Mac worker / MCP) → ALWAYS full (it trades live).
///   • Empty allow-list → OPEN (everyone allowed sees live) — no regression
///     until you populate the list; populating it activates the restriction.
///   • Otherwise → live only for UIDs/emails on the list.
/// </summary>
public static class LiveDataAccess
{
    public static bool CanSeeLive(ClaimsPrincipal? user, IConfiguration config)
    {
        if (user is null) return false;

        // The Mac worker / MCP authenticate with the static ingest token — that
        // path runs the live trading, so it must see live.
        if (string.Equals(user.Identity?.AuthenticationType, IngestTokenAuth.Scheme, StringComparison.Ordinal))
            return true;

        var allow = config.GetSection("Firebase:LiveDataUserIds").Get<string[]>() ?? [];
        if (allow.Length == 0) return true; // opt-in: unconfigured = no change

        var uid = user.FindFirst("user_id")?.Value ?? user.FindFirst("sub")?.Value;
        var email = user.FindFirst("email")?.Value;
        return (uid is not null && allow.Contains(uid, StringComparer.OrdinalIgnoreCase))
            || (email is not null && allow.Contains(email, StringComparer.OrdinalIgnoreCase));
    }

    /// <summary>True if this broker label denotes a LIVE (real-money) account.</summary>
    public static bool IsLiveBroker(string? broker)
        => (broker ?? "").Contains("LIVE", StringComparison.OrdinalIgnoreCase);
}
