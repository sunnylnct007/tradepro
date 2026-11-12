using System.Text.Json;

namespace TradePro.Api.Providers.IBKR;

/// <summary>What one IBKR snapshot says about the live market-data feed.</summary>
public enum LiveFeedState
{
    /// <summary>A real last, bid or ask came back.</summary>
    Live,
    /// <summary>IBKR answered with the previous close ('C' prefix): the market is shut. Not a fault.</summary>
    Closed,
    /// <summary>IBKR reports the instrument halted ('H' prefix).</summary>
    Halted,
    /// <summary>The contract resolved and IBKR returned no value at all. The feed is down.</summary>
    Dark,
    /// <summary>The probe could not reach IBKR (auth, network, contract lookup).</summary>
    Unreachable,
}

public sealed record LiveFeedReading(
    string Symbol,
    long? Conid,
    LiveFeedState State,
    decimal? Last,
    string? Availability,
    DateTime CheckedAtUtc,
    string? Detail);

/// <summary>
/// ONE definition of "is IBKR serving live prices right now", used by the
/// quote endpoint and by anything that must say whether a mark can be trusted.
///
/// Until 12 Nov 2026 the strangle P&amp;L answered that question from the API bar
/// harvester's last cycle ("IBKR served 0 of N symbols"). That measured HISTORY
/// availability, not live prices — the same afternoon history was rate-limited
/// while quotes were fine, so the alarm would have fired falsely — and the
/// harvester itself was retired (docs/DATA_ARCHITECTURE.md step 4). The honest
/// question for a position mark is the one asked here.
/// </summary>
public static class IbkrLiveFeed
{
    /// <summary>Last, bid, ask: the fields whose presence means the feed is live.</summary>
    public const string ProbeFields = "31,84,86";

    /// <summary>Classify one snapshot element. Pure; unit-tested.</summary>
    public static LiveFeedState Classify(JsonElement snapshot)
    {
        if (IbkrQuoteFields.RealOrNull(snapshot, "31") is not null
            || IbkrQuoteFields.RealOrNull(snapshot, "84") is not null
            || IbkrQuoteFields.RealOrNull(snapshot, "86") is not null)
            return LiveFeedState.Live;
        return IbkrQuoteFields.Read(snapshot, "31").How switch
        {
            IbkrQuoteFields.Marker.PreviousClose => LiveFeedState.Closed,
            IbkrQuoteFields.Marker.Halted => LiveFeedState.Halted,
            _ => LiveFeedState.Dark,
        };
    }

    /// <summary>
    /// IBKR's snapshot endpoint SUBSCRIBES on the first call and only returns
    /// fields on a later one: a cold symbol comes back with nothing but its
    /// identifiers. True when that is all the body holds.
    /// </summary>
    public static bool IsColdSnapshot(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return true;
        try
        {
            using var doc = JsonDocument.Parse(raw);
            var el = FirstElement(doc.RootElement);
            if (el.ValueKind != JsonValueKind.Object) return true;
            // Anything beyond the contract identifiers means IBKR answered.
            foreach (var prop in el.EnumerateObject())
            {
                var n = prop.Name;
                if (n is "conid" or "conidEx" or "server_id" or "_updated" or "6119") continue;
                return false;
            }
            return true;
        }
        catch (JsonException)
        {
            return false;   // unparseable is a different problem; let it through
        }
    }

    /// <summary>Probe one symbol: resolve, snapshot (with the cold-subscribe retry), classify. Never throws.</summary>
    public static async Task<LiveFeedReading> ProbeAsync(IBKRClient ibkr, string symbol, CancellationToken ct)
    {
        var sym = symbol.Trim().ToUpperInvariant();
        long? conid = null;
        try
        {
            conid = await ibkr.ResolveConidAsync(sym, "STK", ct);
            if (conid is null)
                return new(sym, null, LiveFeedState.Unreachable, null, null, DateTime.UtcNow,
                           $"{sym} did not resolve at IBKR (secdef/search)");
            var raw = await ibkr.GetSnapshotRawAsync(conid.Value, ProbeFields, ct);
            if (IsColdSnapshot(raw))
            {
                await Task.Delay(TimeSpan.FromMilliseconds(1200), ct);
                raw = await ibkr.GetSnapshotRawAsync(conid.Value, ProbeFields, ct);
            }
            if (string.IsNullOrWhiteSpace(raw))
                return new(sym, conid, LiveFeedState.Unreachable, null, null, DateTime.UtcNow,
                           "IBKR returned no snapshot body");
            using var doc = JsonDocument.Parse(raw);
            var el = FirstElement(doc.RootElement);
            var state = Classify(el);
            var availability = el.TryGetProperty("6509", out var av) ? av.GetString() : null;
            return new(sym, conid, state, IbkrQuoteFields.RealOrNull(el, "31"), availability, DateTime.UtcNow, null);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return new(sym, conid, LiveFeedState.Unreachable, null, null, DateTime.UtcNow,
                       $"{ex.GetType().Name}: {ex.Message}");
        }
    }

    private static JsonElement FirstElement(JsonElement root) =>
        root.ValueKind == JsonValueKind.Array ? root.EnumerateArray().FirstOrDefault() : root;
}
