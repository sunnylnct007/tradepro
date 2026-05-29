/**
 * PositionChartsCard — visual position-tracking: for each held
 * symbol, show the strategy's chart (Ichimoku cloud / RSI / SMA200)
 * so the trader sees price trend, indicators, and our entry context
 * in one glance.
 *
 * Defaults to top 5 by notional (|qty * avg_price|). Toggle to see
 * all, or click an individual symbol to expand. Lazy-loaded Plotly
 * so the cockpit stays light when this card is collapsed.
 *
 * Data source:
 *   - Held positions: T212PosResp (broker = golden source per
 *     project_broker_is_golden_source).
 *   - Charts: latestSessions[].charts (Plotly figure dicts emitted
 *     by strategy.recent_charts()).
 *
 * Symbol matching: strips broker suffixes so the bare ticker matches
 * the chart key. AAPL_US_EQ / CS.D.AAPL.CASH.IP / AAPL all match
 * a chart keyed on 'AAPL'.
 */
import { useState } from "react";
import { CockpitCard } from "../CockpitCard";
import { PlotlyChart } from "../PlotlyChart";
import type { LatestSession, T212PosResp } from "../../types/cockpit";

type Props = {
  positions: T212PosResp | null;
  latestSessions: LatestSession[];
  onHide?: () => void;
};

type Tile = {
  symbol: string;       // bare ticker (AAPL, EURUSD, …)
  brokerTicker: string; // what the broker calls it (AAPL_US_EQ, …)
  qty: number;
  avgPrice: number | null;
  currentPrice: number | null;
  unrealisedAbs: number | null;
  unrealisedPct: number | null;
  notional: number;     // |qty × current_or_avg|
  chartKey: string;     // "ichimoku_cloud:AAPL"
  figure: unknown | null;
};

export function PositionChartsCard({ positions, latestSessions, onHide }: Props) {
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const tiles = buildTiles(positions, latestSessions);
  const visible = showAll ? tiles : tiles.slice(0, 5);

  return (
    <CockpitCard
      id="position-charts"
      title={`Position charts — ${tiles.length} held symbol${tiles.length === 1 ? "" : "s"}`}
      defaultOpen={tiles.length > 0}
      fullWidth
      onHide={onHide}
    >
      {tiles.length === 0 ? (
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          No charted positions. Either the strategy hasn't run a session
          today (so no <code>recent_charts</code>), or the held symbols
          aren't in the strategy's universe. Held positions
          come from the broker directly; charts come from the latest
          strategy session per project_broker_is_golden_source.
        </span>
      ) : (
        <>
          <div style={{ display: "flex", gap: 8, marginBottom: 10, fontSize: 11 }}>
            <span style={{ color: "var(--text-muted)" }}>
              Sorted by notional. Click any tile to expand.
            </span>
            {tiles.length > 5 && (
              <button
                onClick={() => setShowAll((v) => !v)}
                style={{
                  marginLeft: "auto",
                  padding: "2px 10px", fontSize: 11,
                  borderRadius: 999,
                  border: "1px solid var(--border)",
                  background: "transparent",
                  color: "var(--text-dim)",
                  cursor: "pointer",
                }}
              >
                {showAll ? `Show top 5` : `Show all ${tiles.length}`}
              </button>
            )}
          </div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
            gap: 12,
          }}>
            {visible.map((t) => (
              <PositionTile
                key={t.brokerTicker}
                tile={t}
                expanded={expanded.has(t.brokerTicker)}
                onToggle={() => setExpanded((prev) => {
                  const next = new Set(prev);
                  next.has(t.brokerTicker) ? next.delete(t.brokerTicker) : next.add(t.brokerTicker);
                  return next;
                })}
              />
            ))}
          </div>
        </>
      )}
    </CockpitCard>
  );
}

function PositionTile({
  tile, expanded, onToggle,
}: {
  tile: Tile;
  expanded: boolean;
  onToggle: () => void;
}) {
  const pnlColor =
    tile.unrealisedAbs == null ? "var(--text-muted)"
    : tile.unrealisedAbs > 0 ? "var(--up)" : tile.unrealisedAbs < 0 ? "var(--down)" : "var(--text-muted)";
  return (
    <div
      onClick={onToggle}
      style={{
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: "8px 10px",
        background: "rgba(0,0,0,0.10)",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 6 }}>
        <strong style={{ fontSize: 13, color: "var(--text)" }}>
          {tile.symbol}
          <span style={{ color: "var(--text-muted)", fontSize: 10, marginLeft: 6 }}>
            {tile.brokerTicker !== tile.symbol ? tile.brokerTicker : ""}
          </span>
        </strong>
        <span style={{ fontSize: 10, color: "var(--text-dim)" }}>
          {expanded ? "▾ collapse" : "▸ expand"}
        </span>
      </div>
      <div style={{ display: "flex", gap: 12, fontSize: 11, marginBottom: 6, flexWrap: "wrap" }}>
        <span>qty: <strong>{fmt(tile.qty, 4)}</strong></span>
        {tile.avgPrice != null && <span>avg: <strong>{fmt(tile.avgPrice, 4)}</strong></span>}
        {tile.currentPrice != null && <span>now: <strong>{fmt(tile.currentPrice, 4)}</strong></span>}
        {tile.unrealisedAbs != null && (
          <span style={{ color: pnlColor }}>
            P&L: {tile.unrealisedAbs >= 0 ? "+" : ""}{fmt(tile.unrealisedAbs, 2)}
            {tile.unrealisedPct != null && <> ({tile.unrealisedPct >= 0 ? "+" : ""}{tile.unrealisedPct.toFixed(2)}%)</>}
          </span>
        )}
      </div>
      {expanded && tile.figure ? (
        <div style={{ marginTop: 6, height: 280 }} onClick={(e) => e.stopPropagation()}>
          <PlotlyChart figure={tile.figure as Parameters<typeof PlotlyChart>[0]["figure"]} />
        </div>
      ) : null}
      {!expanded && tile.figure ? (
        <div style={{ marginTop: 6, height: 110, overflow: "hidden" }}>
          <PlotlyChart figure={tile.figure as Parameters<typeof PlotlyChart>[0]["figure"]} />
        </div>
      ) : null}
      {!tile.figure && (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
          No strategy chart for this symbol (out of universe or strategy hasn't fired today).
        </div>
      )}
    </div>
  );
}

function buildTiles(positions: T212PosResp | null, latestSessions: LatestSession[]): Tile[] {
  // Aggregate all charts available from any session, keyed by bare symbol.
  const chartBySymbol = new Map<string, unknown>();
  for (const s of latestSessions) {
    for (const [name, fig] of Object.entries(s.charts ?? {})) {
      // chart keys are typically "ichimoku_cloud:AAPL" — pull the
      // symbol off the suffix.
      const idx = name.indexOf(":");
      const sym = idx > 0 ? name.slice(idx + 1).toUpperCase() : name.toUpperCase();
      if (!chartBySymbol.has(sym)) chartBySymbol.set(sym, fig);
    }
  }

  const tiles: Tile[] = [];
  for (const p of positions?.positions ?? []) {
    const bare = bareTicker(p.ticker);
    const figure = chartBySymbol.get(bare.toUpperCase()) ?? null;
    const qty = Number(p.quantity ?? 0);
    if (qty === 0) continue;
    const avg = p.averagePricePaid != null ? Number(p.averagePricePaid) : null;
    const cur = p.currentPrice != null ? Number(p.currentPrice) : null;
    const notional = Math.abs(qty * (cur ?? avg ?? 0));
    tiles.push({
      symbol: bare,
      brokerTicker: p.ticker,
      qty,
      avgPrice: avg,
      currentPrice: cur,
      unrealisedAbs: p.unrealisedAbs ?? null,
      unrealisedPct: p.unrealisedPct ?? null,
      notional,
      chartKey: `ichimoku_cloud:${bare}`,
      figure,
    });
  }
  // Highest-notional first so the top-5 default surfaces our biggest
  // exposure.
  tiles.sort((a, b) => b.notional - a.notional);
  return tiles;
}

function bareTicker(brokerTicker: string): string {
  // AAPL_US_EQ → AAPL, CS.D.EURUSD.MINI.IP → EURUSD, AAPL → AAPL.
  if (brokerTicker.startsWith("CS.D.") || brokerTicker.startsWith("IX.D.")) {
    const parts = brokerTicker.split(".");
    if (parts.length >= 4) return parts[2].toUpperCase();
  }
  const u = brokerTicker.indexOf("_");
  return u > 0 ? brokerTicker.slice(0, u).toUpperCase() : brokerTicker.toUpperCase();
}

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || !isFinite(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}
