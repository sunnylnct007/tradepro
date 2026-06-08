namespace TradePro.Api.Configuration;

/// <summary>
/// Central, CONFIG-DRIVEN broker → account-currency resolution. ONE source of
/// truth (the "BrokerCurrencies" appsettings section) so the account/display
/// currency is not hardcoded per endpoint (the per-place "IG ? GBP : USD" and
/// "?? GBP" literals caused Ichimoku-Equity P&L to render in $ on a GBP account).
///
/// Update the currency in appsettings → it cascades to every consumer
/// (cash-summary, pnl/by-strategy, …) that calls Resolve.
///
/// Resolution order:
///   1. a broker-REPORTED currency (always wins — pass it as `reported`)
///   2. exact broker key            (e.g. "T212_DEMO")
///   3. known prefix                ("T212" / "IG" / "IBKR")
///   4. "BrokerCurrencies:Default"  (final fallback)
/// </summary>
public static class BrokerCurrencies
{
    private static readonly string[] Prefixes = { "IBKR", "T212", "IG" };

    public static string Resolve(IConfiguration config, string? broker, string? reported = null)
    {
        if (!string.IsNullOrWhiteSpace(reported)) return reported!;

        var section = config.GetSection("BrokerCurrencies");
        var b = (broker ?? string.Empty).Trim();

        var exact = section[b];
        if (!string.IsNullOrWhiteSpace(exact)) return exact!;

        foreach (var p in Prefixes)
        {
            if (b.StartsWith(p, StringComparison.OrdinalIgnoreCase))
            {
                var pv = section[p];
                if (!string.IsNullOrWhiteSpace(pv)) return pv!;
            }
        }

        return section["Default"] ?? "USD";
    }
}
