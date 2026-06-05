using Dapper;
using Npgsql;
using TradePro.Api.Models;

namespace TradePro.Api.Simulation;

/// <summary>
/// Phase C-3 — layers the catalyst registry onto SignalDecision rows
/// so the cockpit decision row + Decide page surface event-driven
/// context alongside the technical verdict. North-star item from
/// project memory: pure-technical signals miss event-driven trades
/// (Ecopetrol 2026-05-21 — tech said WAIT, real trade was BUY
/// because of Colombia election + oil at $105).
///
/// Design constraints:
///
///   * Never silently overrides Action. The trader keeps agency;
///     TradePro adds visibility. The CatalystFlag is a coarse
///     "tech_event_divergence" / "tech_event_alignment" / null
///     hint — the trader decides what to do with it.
///
///   * Always populates Catalysts (empty list when nothing active)
///     so the frontend can render unconditionally without null
///     guards.
///
///   * Batched. EnrichManyAsync issues one query for the whole
///     scan instead of N — a 100-symbol scan touches the registry
///     once.
///
///   * Cheap. The catalysts table's partial index
///     `catalysts_active_window` covers the WHERE clause; the per-
///     symbol roll-up runs in C# from a small result set.
/// </summary>
public interface ICatalystEnricher
{
    Task<SignalDecision> EnrichOneAsync(SignalDecision decision, CancellationToken ct);
    Task<IReadOnlyList<SignalDecision>> EnrichManyAsync(
        IReadOnlyList<SignalDecision> decisions, CancellationToken ct);
}

public sealed class CatalystEnricher : ICatalystEnricher
{
    private readonly NpgsqlDataSource _db;
    private readonly int _windowDays;

    // Severity threshold for the divergence/alignment chip.
    // Currently HIGH only — MEDIUM catalysts surface as chips but
    // don't tip the flag. Tunable via constructor when the cockpit
    // exposes a "sensitivity" pill (deferred to C-3.1).
    private const string HighSeverity = "high";

    // Days-until threshold for "imminent" catalysts. A HIGH catalyst
    // 30 days out matters less than one in 7 days. The flag fires
    // only when DaysUntil is within this window AND non-negative
    // (past catalysts don't trip the flag — they're informational).
    private const int ImminentDays = 14;

    public CatalystEnricher(NpgsqlDataSource db, int windowDays = 30)
    {
        _db = db;
        // Clamp so a misconfiguration can't sweep years of catalysts.
        _windowDays = Math.Clamp(windowDays, 1, 90);
    }

    public async Task<SignalDecision> EnrichOneAsync(
        SignalDecision decision, CancellationToken ct)
    {
        var enriched = await EnrichManyAsync(new[] { decision }, ct);
        return enriched[0];
    }

    public async Task<IReadOnlyList<SignalDecision>> EnrichManyAsync(
        IReadOnlyList<SignalDecision> decisions, CancellationToken ct)
    {
        if (decisions.Count == 0) return decisions;

        // De-dup symbols (some strategies emit two decisions for the
        // same symbol — e.g. paired long/short) so the registry query
        // is minimal.
        var symbols = decisions.Select(d => d.Symbol).Distinct().ToArray();

        await using var conn = await _db.OpenConnectionAsync(ct);
        // Pull every active catalyst for the requested symbols in the
        // configured window. The partial index `catalysts_active_window`
        // (migration 038) keeps this fast.
        var rows = (await conn.QueryAsync<CatalystRow>(@"
            SELECT id AS Id, symbol AS Symbol, kind AS Kind,
                   occurs_on AS OccursOn,
                   title AS Title, source AS Source, severity AS Severity
            FROM catalysts
            WHERE status = 'active'
              AND symbol = ANY(@symbols)
              AND (occurs_on IS NULL
                   OR (occurs_on >= CURRENT_DATE - (@windowDays || ' days')::INTERVAL
                       AND occurs_on <= CURRENT_DATE + (@windowDays || ' days')::INTERVAL))
            ORDER BY
                CASE severity
                    WHEN 'high'   THEN 0
                    WHEN 'medium' THEN 1
                    WHEN 'low'    THEN 2
                    ELSE 3
                END,
                occurs_on NULLS LAST,
                symbol;",
            new { symbols, windowDays = _windowDays.ToString() })).ToList();

        // Group by symbol so EnrichOne is O(1) lookup.
        var bySymbol = rows
            .GroupBy(r => r.Symbol, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(g => g.Key, g => g.ToList(), StringComparer.OrdinalIgnoreCase);

        var today = DateTime.UtcNow.Date;
        var output = new List<SignalDecision>(decisions.Count);
        foreach (var decision in decisions)
        {
            if (!bySymbol.TryGetValue(decision.Symbol, out var symbolRows) || symbolRows.Count == 0)
            {
                // Always populate Catalysts as empty (not null) so the
                // frontend can render unconditionally.
                output.Add(decision with {
                    Catalysts = Array.Empty<CatalystSummary>(),
                    CatalystFlag = null,
                });
                continue;
            }

            var catalysts = symbolRows
                .Select(r => new CatalystSummary(
                    Id: r.Id,
                    Kind: r.Kind,
                    OccursOn: r.OccursOn?.ToString("yyyy-MM-dd"),
                    Title: r.Title,
                    Severity: r.Severity,
                    Source: r.Source,
                    DaysUntil: r.OccursOn is DateTime d
                        ? (int)(d.Date - today).TotalDays
                        : null))
                .ToList();

            var flag = ComputeFlag(decision.Action, catalysts);
            output.Add(decision with {
                Catalysts = catalysts,
                CatalystFlag = flag,
            });
        }

        return output;
    }

    /// <summary>The Ecopetrol logic. HOLD + imminent high-severity
    /// catalyst → divergence. BUY + imminent high-severity → alignment.
    /// Everything else → null (chips still render, no chip-level
    /// flag).</summary>
    public static string? ComputeFlag(
        string action, IReadOnlyList<CatalystSummary> catalysts)
    {
        if (catalysts.Count == 0) return null;

        // "Imminent high" = severity high AND occurs within the
        // configured window AND not yet past.
        bool hasImminentHigh = catalysts.Any(c =>
            string.Equals(c.Severity, HighSeverity, StringComparison.OrdinalIgnoreCase)
            && c.DaysUntil is int dt
            && dt >= 0 && dt <= ImminentDays);

        if (!hasImminentHigh) return null;

        var actionUpper = (action ?? "").ToUpperInvariant();
        return actionUpper switch
        {
            "HOLD" => "tech_event_divergence",
            "BUY"  => "tech_event_alignment",
            // SELL with an imminent high catalyst is ambiguous —
            // could be the catalyst confirming the exit, could be
            // the catalyst contradicting it. We surface the chips
            // but stay silent on the flag rather than guess.
            _      => null,
        };
    }

    // OccursOn maps to a Postgres DATE column. Dapper hands it back as
    // DateTime (midnight UTC) — we convert to DateOnly + days-until in
    // EnrichManyAsync. Using DateTime? here avoids a Dapper materialisation
    // mismatch on older Npgsql versions that don't surface DateOnly.
    private sealed record CatalystRow(
        long Id,
        string Symbol,
        string Kind,
        DateTime? OccursOn,
        string Title,
        string Source,
        string Severity);
}
