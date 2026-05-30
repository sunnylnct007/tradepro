/**
 * PositionsPanel — the cockpit's "Overall position" card, organised by
 * PRODUCT TYPE so the trader sees the whole book on one screen.
 *
 * Groups today:
 *   - Equity (T212)  — live mark + unrealised P&L
 *   - FX     (IG)    — entry level + direction (IG's positions endpoint
 *                      has no live mark, so no P&L column here)
 *
 * Each product is its own <ProductGroup> with the columns that make
 * sense for it. Adding a new asset class later (crypto, CFD index, …)
 * is a localised change: add a loader + one <ProductGroup> block — the
 * card, badge, and layout don't change. This is deliberately a separate
 * component so TraderCockpit doesn't keep growing an inline positions
 * table per broker.
 *
 * T212 equity positions are passed in as a prop because the cockpit's
 * KpiStrip + PositionChartsCard already consume them. IG/FX positions
 * are fetched here since nothing else needs them.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";
import type { T212PosResp } from "../../types/cockpit";

type IGPosResp = Awaited<ReturnType<typeof api.igPositions>>;

const posTh: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", fontWeight: 600,
  fontSize: 11, textTransform: "uppercase", letterSpacing: "0.04em",
};
const posTd: React.CSSProperties = { padding: "4px 8px" };
const numTd: React.CSSProperties = { ...posTd, textAlign: "right", fontFamily: "monospace" };

const UP = "#1fc16b";
const DOWN = "#ef4444";

export function PositionsPanel({
  positions,
  posErr,
  account,
  onHide,
}: {
  positions: T212PosResp | null;
  posErr: string | null;
  account: "demo" | "live";
  onHide: () => void;
}) {
  const [ig, setIg] = useState<IGPosResp | null>(null);
  const [igErr, setIgErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const d = await api.igPositions();
        if (cancelled) return;
        setIg(d);
        setIgErr(null);
      } catch (e) {
        if (cancelled) return;
        setIgErr(String(e));
      }
    };
    void load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const equityCount = positions?.enabled ? positions.positionCount : 0;
  const fxCount = ig?.enabled ? ig.positions.length : 0;
  const total = equityCount + fxCount;

  return (
    <CockpitCard
      id="positions"
      title="Overall position"
      badge={total || undefined}
      onHide={onHide}
    >
      <ProductGroup
        label={`Equity · T212 (${account})`}
        error={posErr}
        loading={!positions}
        connected={!!positions?.enabled}
        empty={equityCount === 0}
        notConnectedText={`T212 ${account} not connected.`}
        emptyText={`No open positions in T212 ${account}.`}
        first
      >
        <table style={tableStyle}>
          <thead>
            <tr style={{ color: "var(--text-dim)" }}>
              <th style={posTh}>Ticker</th>
              <th style={rightTh}>Qty</th>
              <th style={rightTh}>Avg cost</th>
              <th style={rightTh}>Now</th>
              <th style={rightTh}>P&L %</th>
              <th style={rightTh}>P&L</th>
            </tr>
          </thead>
          <tbody>
            {positions?.positions.map((p) => (
              <tr key={p.ticker} style={rowStyle}>
                <td style={posTd}>{p.ticker}</td>
                <td style={numTd}>{p.quantity}</td>
                <td style={numTd}>{p.averagePricePaid?.toFixed(4) ?? "—"}</td>
                <td style={numTd}>{p.currentPrice?.toFixed(4) ?? "—"}</td>
                <td style={{ ...numTd, color: (p.unrealisedPct ?? 0) >= 0 ? UP : DOWN }}>
                  {p.unrealisedPct != null ? `${p.unrealisedPct >= 0 ? "+" : ""}${p.unrealisedPct.toFixed(2)}%` : "—"}
                </td>
                <td style={{ ...numTd, color: (p.unrealisedAbs ?? 0) >= 0 ? UP : DOWN }}>
                  {p.unrealisedAbs != null ? `${p.unrealisedAbs >= 0 ? "+" : ""}${p.unrealisedAbs.toFixed(2)}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ProductGroup>

      <ProductGroup
        label={`FX · IG${ig?.mode ? ` (${ig.mode})` : ""}`}
        error={igErr ?? ig?.error ?? null}
        loading={!ig}
        connected={!!ig?.enabled}
        empty={fxCount === 0}
        notConnectedText="IG not connected."
        emptyText="No open FX positions in IG."
      >
        <table style={tableStyle}>
          <thead>
            <tr style={{ color: "var(--text-dim)" }}>
              <th style={posTh}>Instrument</th>
              <th style={rightTh}>Qty</th>
              <th style={rightTh}>Entry</th>
              <th style={rightTh}>Side</th>
            </tr>
          </thead>
          <tbody>
            {ig?.positions.map((p) => (
              <tr key={p.dealId ?? p.ticker} style={rowStyle}>
                <td style={posTd} title={p.ticker}>{p.instrumentName || p.ticker}</td>
                <td style={numTd}>{Math.abs(p.quantity)}</td>
                <td style={numTd}>{p.averagePricePaid?.toFixed(4) ?? "—"}</td>
                <td style={{ ...numTd, color: p.quantity >= 0 ? UP : DOWN }}>
                  {p.quantity >= 0 ? "LONG" : "SHORT"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ProductGroup>

      <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
        Detailed view: <Link to="/portfolio" style={{ color: "var(--text-muted)" }}>Portfolio →</Link>
        {" · "}Per-order drill-in: <Link to="/oms" style={{ color: "var(--text-muted)" }}>OMS →</Link>
      </div>
    </CockpitCard>
  );
}

/**
 * One product-type section: a labelled header plus the loading / not-
 * connected / empty / error states shared by every product. When the
 * book has rows, `children` (the product-specific table) is rendered.
 */
function ProductGroup({
  label,
  error,
  loading,
  connected,
  empty,
  notConnectedText,
  emptyText,
  first,
  children,
}: {
  label: string;
  error: string | null;
  loading: boolean;
  connected: boolean;
  empty: boolean;
  notConnectedText: string;
  emptyText: string;
  first?: boolean;
  children: React.ReactNode;
}) {
  const muted = { fontSize: 12, color: "var(--text-muted)" } as const;
  return (
    <div style={first ? undefined : { marginTop: 14, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
      <div style={{
        fontSize: 11, fontWeight: 700, color: "var(--text-dim)",
        letterSpacing: "0.04em", textTransform: "uppercase", marginBottom: 6,
      }}>
        {label}
      </div>
      {error ? (
        <span style={{ fontSize: 12, color: DOWN }}>fetch failed: {error}</span>
      ) : loading ? (
        <span style={muted}>Loading…</span>
      ) : !connected ? (
        <span style={muted}>{notConnectedText}</span>
      ) : empty ? (
        <span style={muted}>{emptyText}</span>
      ) : (
        children
      )}
    </div>
  );
}

const tableStyle: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 12 };
const rightTh: React.CSSProperties = { ...posTh, textAlign: "right" };
const rowStyle: React.CSSProperties = { borderTop: "1px solid var(--border)" };
