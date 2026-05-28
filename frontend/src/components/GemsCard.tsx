import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { CompareLatestResponse, CompareRow, CompareUniverseSummary } from "../api/types";

/**
 * Phase G: contrarian / mean-reversion picks rendered on the Decide
 * page. Pulls every row across cached universes where
 * `gem_verdict.is_gem == true`, dedupes by symbol (best score wins),
 * and renders one card per gem with the full v2 audit trail.
 *
 * Gems are RARE by design — the v2 rules are conservative. When zero
 * gems match (typical in a momentum regime where the broad market is
 * at 52w highs), we render an explicit empty state explaining
 * exactly that, instead of hiding the card. Surface absence is itself
 * a verdict.
 */

interface GemRow {
  symbol: string;
  universe: string;
  row: CompareRow;
}

const PROFILE_LABEL = { stock: "Stock", etf: "ETF" } as const;

const RISK_COLOUR: Record<string, string> = {
  HIGH: "var(--down)",
  EXTREME: "#c34cff",
};

export function GemsCard() {
  const [universes, setUniverses] = useState<CompareUniverseSummary[]>([]);
  const [universeRows, setUniverseRows] = useState<Record<string, CompareRow[]>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.compareUniverses()
      .then((r) => { if (!cancelled) setUniverses(r.universes); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (universes.length === 0) return;
    let cancelled = false;
    Promise.all(
      universes.map((u) =>
        api.compareLatest(u.universe).then(
          (r: CompareLatestResponse) =>
            [u.universe, (r.payload?.rows ?? []) as CompareRow[]] as [string, CompareRow[]],
        ).catch(() => [u.universe, [] as CompareRow[]] as [string, CompareRow[]]),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      const next: Record<string, CompareRow[]> = {};
      for (const [name, rows] of pairs) next[name] = rows;
      setUniverseRows(next);
    });
    return () => { cancelled = true; };
  }, [universes]);

  // Best gem per symbol across all universes — score wins, then rank.
  const gems = useMemo<GemRow[]>(() => {
    const m = new Map<string, GemRow>();
    for (const [universe, rows] of Object.entries(universeRows)) {
      for (const r of rows) {
        if (!r.gem_verdict?.is_gem) continue;
        const sym = (r.symbol ?? "").toUpperCase();
        if (!sym) continue;
        const score = r.gem_verdict.score ?? 0;
        const existing = m.get(sym);
        if (!existing || (existing.row.gem_verdict?.score ?? 0) < score) {
          m.set(sym, { symbol: sym, universe, row: r });
        }
      }
    }
    return Array.from(m.values()).sort(
      (a, b) => (b.row.gem_verdict?.score ?? 0) - (a.row.gem_verdict?.score ?? 0),
    );
  }, [universeRows]);

  // Sector concentration — if ≥50% of gems share a sector/universe,
  // surface as a banner rather than hiding individual gems. Keeps
  // the dashboard honest about regime signals.
  const concentrationBanner = useMemo(() => {
    if (gems.length < 3) return null;
    const counts: Record<string, number> = {};
    for (const g of gems) {
      const k = g.universe;
      counts[k] = (counts[k] ?? 0) + 1;
    }
    const [topKey, topCount] = Object.entries(counts).sort(
      (a, b) => b[1] - a[1],
    )[0] ?? ["", 0];
    if (topCount / gems.length >= 0.5 && topKey) {
      return (
        `${topCount} of ${gems.length} gems are in ${topKey}. ` +
        "May indicate sector rotation rather than stock-specific opportunities — " +
        "consider sector ETF for cleaner exposure."
      );
    }
    return null;
  }, [gems]);

  if (error || (universes.length === 0)) return null;

  return (
    <section className="card" style={{ borderLeft: "3px solid var(--accent, #4f8cff)" }}>
      <header
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 10,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>
            Gem hunter — contrarian / deep-value picks
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 400, marginLeft: 8 }}>
              {gems.length === 0 ? "0 today" : `${gems.length} today`}
            </span>
          </h2>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
            Names down ≥25% from 5y peak, in lower 25th pctile of 52w range, CHEAP per
            basket, with at least one recovery signal firing. Auto-bumped to ≥HIGH risk;
            position caps halved (LOW=12% / MED=8% / HIGH=5% / EXTREME=2%).
          </div>
        </div>
      </header>

      {concentrationBanner && (
        <div style={{
          padding: "8px 12px",
          marginBottom: 10,
          background: "rgba(232,162,58,0.10)",
          borderLeft: "3px solid var(--neutral)",
          borderRadius: 4,
          fontSize: 12,
          color: "var(--text-dim)",
        }}>
          ⚠ {concentrationBanner}
        </div>
      )}

      {gems.length === 0 && <EmptyState />}

      {gems.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {gems.map((g) => (
            <GemRow key={g.symbol} gem={g} />
          ))}
        </ul>
      )}

      <div style={{ marginTop: 12, fontSize: 10, color: "var(--text-muted)", lineHeight: 1.5 }}>
        Caveat: gems can be value traps — quality breakdown, regime change, sector-
        specific shocks. The v2 rules filter for Sharpe ≥ 0.5 (ETF) / 0.7 (stock),
        max-DD recovery ≤ 24mo, sentiment mean ≥ −0.15 (zero very-negs, ≤1
        material-neg), and require ≥2 recovery signals on stocks. Pair with the
        per-symbol detail before sizing.
      </div>
    </section>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        padding: "16px 18px",
        background: "rgba(0,0,0,0.10)",
        border: "1px dashed var(--border)",
        borderRadius: 6,
        fontSize: 13,
        color: "var(--text-dim)",
        lineHeight: 1.55,
      }}
    >
      <strong style={{ color: "var(--text)" }}>No gems matching the contrarian profile today.</strong>
      <div style={{ marginTop: 4 }}>
        Today's market is broadly near 52w highs — deep-value setups are scarce in a
        momentum regime. Most names are either too close to highs (range_pct &gt; 25th)
        or haven't pulled back enough (DD &gt; −25%). The system is designed to surface
        gems sparingly; absence is itself a regime read. Re-check after a meaningful
        broad pullback.
      </div>
    </div>
  );
}

function GemRow({ gem }: { gem: GemRow }) {
  const v = gem.row.gem_verdict!;
  const ms = gem.row.market_state;
  const dd = ms.drawdown_from_peak_pct;
  const rp = ms.range_position_pct;
  const rsi = ms.rsi_14;
  const profileLabel = PROFILE_LABEL[v.profile] ?? v.profile;
  const riskColour = v.forced_risk ? RISK_COLOUR[v.forced_risk] : "var(--text-dim)";

  return (
    <li style={{ padding: "12px 0", borderTop: "1px solid var(--border, rgba(37,50,86,0.4))" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 6 }}>
        <span style={{ fontSize: 16, fontWeight: 700 }}>{gem.symbol}</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{gem.universe}</span>
        <span
          style={{
            fontSize: 10, padding: "1px 6px", borderRadius: 3,
            border: "1px solid var(--border)", color: "var(--text-dim)",
          }}
        >
          {profileLabel}
        </span>
        <span
          style={{
            fontSize: 10, padding: "1px 6px", borderRadius: 3,
            border: `1px solid ${riskColour}`, color: riskColour, fontWeight: 700,
            letterSpacing: "0.05em",
          }}
          title={`Auto-bumped to ${v.forced_risk}; advisory cap ${v.position_cap_pct}% of portfolio`}
        >
          RISK · {v.forced_risk ?? "—"}  ·  cap {v.position_cap_pct}%
        </span>
        <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: "auto" }}>
          score {v.score}/8
        </span>
      </div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, color: "var(--text-dim)" }}>
        <span>
          DD from 5y peak{" "}
          <strong className="num" style={{ color: "var(--text)" }}>
            {dd !== null && dd !== undefined ? `${dd.toFixed(1)}%` : "—"}
          </strong>
        </span>
        <span>
          range{" "}
          <strong className="num" style={{ color: "var(--text)" }}>
            {rp !== null && rp !== undefined ? `${rp.toFixed(0)}th pctile` : "—"}
          </strong>
        </span>
        <span>
          RSI{" "}
          <strong className="num" style={{ color: "var(--text)" }}>
            {rsi !== null && rsi !== undefined ? rsi.toFixed(0) : "—"}
          </strong>
        </span>
      </div>
      <ul style={{ margin: "8px 0 0 0", padding: 0, listStyle: "none", fontSize: 12 }}>
        {v.reasons.passing.slice(0, 8).map((reason, i) => (
          <li key={i} style={{ color: "var(--text-dim)", padding: "1px 0" }}>
            <span style={{ color: "var(--up)", marginRight: 6 }}>✓</span>
            {reason}
          </li>
        ))}
      </ul>
    </li>
  );
}
