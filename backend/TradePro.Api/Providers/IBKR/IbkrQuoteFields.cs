using System.Globalization;
using System.Text.Json;

namespace TradePro.Api.Providers.IBKR;

/// <summary>
/// Reading IBKR snapshot fields WITHOUT mistaking "I don't have this" for a price.
///
/// IBKR answers a field it cannot serve with the literal string <c>"N/A"</c>,
/// and a snapshot full of them comes back as a perfectly ordinary HTTP 200.
/// That is the most dangerous shape of answer this provider gives, because it
/// looks exactly like success: on 18 Aug 2026 the health probe reported
/// "ok — auth + live snapshot" for an entire trading day while every symbol was
/// dark and the wheel board ran 100% on carried prices (fixed in 22a7dcc,
/// Python side). The predicate lived only in that probe; nothing stopped the
/// C# quote endpoint from handing callers the same confident empty quote.
///
/// It also refuses a value IBKR has explicitly marked as NOT live. Field 31
/// (last) may come back prefixed — <c>C</c> for "this is the previous close"
/// and <c>H</c> for halted. Those are real numbers and completely wrong to
/// render as a live print, which is the whole failure this endpoint exists to
/// prevent. A close is available from the bar store; what the caller asked for
/// here is what is trading now.
/// </summary>
public static class IbkrQuoteFields
{
    /// <summary>Values IBKR uses to mean "no value".</summary>
    private static readonly HashSet<string> NotAValue = new(StringComparer.OrdinalIgnoreCase)
    {
        "", "N/A", "NA", "-", "NONE", "NULL",
    };

    /// <summary>
    /// True when <paramref name="raw"/> is a value rather than IBKR's way of
    /// saying it has none. Mirrors the `_real()` guard in
    /// `cli/ibkr_health_probe.py` — keep the two in step.
    /// </summary>
    public static bool IsRealValue(string? raw)
        => raw is not null && !NotAValue.Contains(raw.Trim());

    /// <summary>
    /// The numeric value of an IBKR snapshot field, or null when the field is
    /// absent, is one of IBKR's "no value" tokens, is flagged as not-live
    /// (<c>C</c>/<c>H</c> prefixed), or does not parse.
    ///
    /// Null is deliberate: it forces the caller to render "no price" instead of
    /// a number it cannot stand behind.
    /// </summary>
    /// <summary>How IBKR qualified a value it did return.</summary>
    public enum Marker
    {
        /// <summary>No usable value at all — absent, or one of IBKR's "no value" tokens.</summary>
        None,
        /// <summary>A live print.</summary>
        Live,
        /// <summary>"C"-prefixed: this is the PREVIOUS CLOSE, not a live print.</summary>
        PreviousClose,
        /// <summary>"H"-prefixed: the instrument is halted.</summary>
        Halted,
    }

    /// <summary>
    /// The value AND how IBKR qualified it, so a caller can tell "the market is
    /// shut" apart from "somebody else is holding the market-data session".
    ///
    /// Those two look identical if you only ask "is there a live price?" — both
    /// answer no — and they have completely different remedies. Reporting
    /// session contention pre-market sends the reader to close a portal that was
    /// never the problem, which is the same class of misdirection as the
    /// "not subscribed" wording this file replaced.
    /// </summary>
    public static (decimal? Value, Marker How) Read(JsonElement snapshot, string fieldId)
    {
        if (snapshot.ValueKind != JsonValueKind.Object) return (null, Marker.None);
        if (!snapshot.TryGetProperty(fieldId, out var prop)) return (null, Marker.None);

        string? raw = prop.ValueKind switch
        {
            JsonValueKind.String => prop.GetString(),
            JsonValueKind.Number => prop.GetRawText(),
            _ => null,
        };
        if (!IsRealValue(raw)) return (null, Marker.None);

        var s = raw!.Trim();
        var how = Marker.Live;
        if (s.Length > 0 && (s[0] is 'C' or 'c')) { how = Marker.PreviousClose; s = s[1..]; }
        else if (s.Length > 0 && (s[0] is 'H' or 'h')) { how = Marker.Halted; s = s[1..]; }

        s = s.TrimStart('D', 'd').Replace(",", "");
        return decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var v)
            ? (v, how)
            : (null, Marker.None);
    }

    public static decimal? RealOrNull(JsonElement snapshot, string fieldId)
    {
        if (snapshot.ValueKind != JsonValueKind.Object) return null;
        if (!snapshot.TryGetProperty(fieldId, out var prop)) return null;

        string? raw = prop.ValueKind switch
        {
            JsonValueKind.String => prop.GetString(),
            JsonValueKind.Number => prop.GetRawText(),
            _ => null,
        };
        if (!IsRealValue(raw)) return null;

        var s = raw!.Trim();

        // NOT a live print. 'C' = previous close, 'H' = halted. Returning these
        // as the last price is exactly the "stale number presented as current"
        // this endpoint refuses to do.
        if (s.Length > 0 && (s[0] is 'C' or 'c' or 'H' or 'h')) return null;

        // IBKR also prefixes some values to denote a delayed feed.
        s = s.TrimStart('D', 'd').Replace(",", "");

        return decimal.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out var v)
            ? v
            : null;
    }
}
