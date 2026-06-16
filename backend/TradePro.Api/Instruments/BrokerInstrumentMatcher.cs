using TradePro.Api.Oms;

namespace TradePro.Api.Instruments;

/// <summary>How a universe symbol was resolved to a broker ticker.</summary>
public enum MatchMethod
{
    Unresolved = 0,
    ByIsin,
    Exact,
    ByName,
}

/// <summary>One resolved (or unresolved) universe symbol.</summary>
public sealed record InstrumentMatchResult(
    string SourceTicker,
    string? BrokerTicker,
    string? Isin,
    MatchMethod Method);

/// <summary>
/// PURE, broker-agnostic matcher: given a broker's neutral catalog and a
/// list of universe symbols, decide each symbol's broker ticker. No DB,
/// no network, no broker-specific literals — the only broker knowledge it
/// borrows is <see cref="SymbolHarmonization.BareSourceTicker"/> (the
/// existing root-extraction helper), so "AAPL_US_EQ" reduces to "AAPL"
/// without this file ever spelling "_US_EQ".
///
/// Resolution priority (never guesses — 0 or &gt;1 candidates = unresolved):
///   1. ISIN          — the durable cross-broker identity, when known.
///   2. Exact ticker  — catalog instrument whose bare root == source root.
///   3. Name match    — exactly one catalog Name normalizes-equal.
/// </summary>
public static class BrokerInstrumentMatcher
{
    public static IReadOnlyList<InstrumentMatchResult> Resolve(
        IReadOnlyList<BrokerInstrument> catalog,
        IReadOnlyList<(string ticker, string? name, string? isin)> universe)
    {
        // Pre-index the catalog once for O(1) lookups.

        // ISIN → instruments (an ISIN can list multiple lines, e.g. share
        // classes / venues; we still demand exactly one to map).
        var byIsin = new Dictionary<string, List<BrokerInstrument>>(StringComparer.OrdinalIgnoreCase);
        // bare root ("AAPL") → instruments sharing that root.
        var byRoot = new Dictionary<string, List<BrokerInstrument>>(StringComparer.OrdinalIgnoreCase);
        // normalized name → instruments.
        var byName = new Dictionary<string, List<BrokerInstrument>>(StringComparer.Ordinal);

        foreach (var inst in catalog)
        {
            if (string.IsNullOrWhiteSpace(inst.BrokerTicker)) continue;

            if (!string.IsNullOrWhiteSpace(inst.Isin))
                Add(byIsin, inst.Isin.Trim(), inst);

            var root = SymbolHarmonization.BareSourceTicker(inst.BrokerTicker);
            if (!string.IsNullOrWhiteSpace(root))
                Add(byRoot, root, inst);

            // Also index by the broker's SHORT ticker root. For most names
            // this equals the order-code root (no-op). For RE-TICKERED names
            // it differs and is the only bridge: T212 lists Jefferies as
            // order-code "LUK_US_EQ" (root "LUK") but shortName "JEF", and the
            // universe speaks "JEF". Both point at the same instrument, so a
            // universe "JEF" resolves to "LUK_US_EQ" by exact ticker — no ISIN
            // or low-confidence name match needed.
            var shortRoot = SymbolHarmonization.BareSourceTicker(inst.ShortName ?? string.Empty);
            if (!string.IsNullOrWhiteSpace(shortRoot)
                && !string.Equals(shortRoot, root, StringComparison.OrdinalIgnoreCase))
                Add(byRoot, shortRoot, inst);

            var norm = NormalizeName(inst.Name);
            if (!string.IsNullOrWhiteSpace(norm))
                Add(byName, norm, inst);
        }

        var results = new List<InstrumentMatchResult>(universe.Count);
        foreach (var (ticker, name, isin) in universe)
        {
            var source = (ticker ?? string.Empty).Trim().ToUpperInvariant();
            if (source.Length == 0) continue;

            // (a) ISIN — preferred cross-broker bridge.
            if (!string.IsNullOrWhiteSpace(isin)
                && byIsin.TryGetValue(isin.Trim(), out var isinHits))
            {
                var pick = PickOne(isinHits);
                if (pick is not null)
                {
                    results.Add(new InstrumentMatchResult(
                        source, pick.BrokerTicker, pick.Isin, MatchMethod.ByIsin));
                    continue;
                }
            }

            // (b) Exact ticker via bare root, broker-agnostic.
            var sourceRoot = SymbolHarmonization.BareSourceTicker(source);
            if (byRoot.TryGetValue(sourceRoot, out var rootHits))
            {
                var pick = PickOne(rootHits);
                if (pick is not null)
                {
                    results.Add(new InstrumentMatchResult(
                        source, pick.BrokerTicker, pick.Isin, MatchMethod.Exact));
                    continue;
                }
            }

            // (c) Name match — only when EXACTLY ONE instrument matches.
            var nname = NormalizeName(name);
            if (!string.IsNullOrWhiteSpace(nname)
                && byName.TryGetValue(nname, out var nameHits)
                && nameHits.Count == 1)
            {
                var pick = nameHits[0];
                results.Add(new InstrumentMatchResult(
                    source, pick.BrokerTicker, pick.Isin, MatchMethod.ByName));
                continue;
            }

            results.Add(new InstrumentMatchResult(source, null, null, MatchMethod.Unresolved));
        }

        return results;
    }

    /// <summary>
    /// From multiple candidates sharing an ISIN or ticker root, prefer a
    /// US equity, then any equity, then the first. Returns null only when
    /// the bucket is empty (never happens for a populated bucket) — so a
    /// non-empty root/ISIN bucket always resolves, intentionally: a shared
    /// root with several listings is still the SAME security, and we have
    /// a deterministic preference. (Name matching is the strict one that
    /// refuses ambiguity, because names collide across distinct issuers.)
    /// </summary>
    private static BrokerInstrument? PickOne(List<BrokerInstrument> hits)
    {
        if (hits.Count == 0) return null;
        if (hits.Count == 1) return hits[0];

        var usEquity = hits.FirstOrDefault(h =>
            IsEquity(h)
            && SymbolHarmonization.BareSourceTicker(h.BrokerTicker) != h.BrokerTicker
            && h.BrokerTicker.Contains("_US", StringComparison.OrdinalIgnoreCase));
        if (usEquity is not null) return usEquity;

        var equity = hits.FirstOrDefault(IsEquity);
        if (equity is not null) return equity;

        return hits[0];
    }

    private static bool IsEquity(BrokerInstrument i)
        => string.Equals(i.AssetClass, "EQUITY", StringComparison.OrdinalIgnoreCase);

    private static void Add(
        Dictionary<string, List<BrokerInstrument>> map, string key, BrokerInstrument inst)
    {
        if (!map.TryGetValue(key, out var list))
        {
            list = new List<BrokerInstrument>(1);
            map[key] = list;
        }
        list.Add(inst);
    }

    private static readonly string[] Suffixes =
    {
        "CORPORATION", "CORP", "INCORPORATED", "INC", "COMPANY", "CO",
        "LIMITED", "LTD", "PLC", "HOLDINGS", "HOLDING", "GROUP",
        "CLASS A", "CLASS B", "CLASS C", "THE",
    };

    /// <summary>
    /// Normalize a company name for cross-source comparison: uppercase,
    /// strip punctuation to spaces, drop common corporate-form suffixes
    /// (INC/CORP/LTD/PLC/HOLDINGS/GROUP/CLASS A·B·C/THE), collapse
    /// whitespace. Iterates suffix-stripping to a fixed point so
    /// "Foo Holdings Group" and "Foo Group Holdings" both reduce to "FOO".
    /// Returns "" for null/blank input.
    /// </summary>
    public static string NormalizeName(string? name)
    {
        if (string.IsNullOrWhiteSpace(name)) return string.Empty;

        var sb = new System.Text.StringBuilder(name.Length);
        foreach (var ch in name.ToUpperInvariant())
        {
            if (char.IsLetterOrDigit(ch)) sb.Append(ch);
            else sb.Append(' ');
        }

        var tokens = sb.ToString()
            .Split(' ', StringSplitOptions.RemoveEmptyEntries)
            .ToList();

        // Iterate to a stable fixed point: a removed suffix may expose
        // another (e.g. "... HOLDINGS GROUP").
        bool changed = true;
        while (changed && tokens.Count > 0)
        {
            changed = false;
            var joined = string.Join(' ', tokens);
            foreach (var suf in Suffixes)
            {
                // Drop "THE" anywhere; drop the rest only as a trailing word/phrase.
                if (suf == "THE")
                {
                    var before = tokens.Count;
                    tokens.RemoveAll(t => t == "THE");
                    if (tokens.Count != before) { changed = true; break; }
                    continue;
                }

                if (joined == suf) // name was ONLY a suffix word — keep it, can't reduce to empty
                    continue;

                if (joined.EndsWith(" " + suf, StringComparison.Ordinal))
                {
                    var cut = joined.Length - suf.Length - 1;
                    tokens = joined[..cut]
                        .Split(' ', StringSplitOptions.RemoveEmptyEntries)
                        .ToList();
                    changed = true;
                    break;
                }
            }
        }

        return string.Join(' ', tokens);
    }
}
