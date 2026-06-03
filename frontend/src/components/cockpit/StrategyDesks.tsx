/**
 * StrategyDesks — the desks-first home. Each strategy is a "desk" / trader
 * with its own book (one broker × one asset class), so the trader sees at a
 * glance how each strategy is doing. Attribution key is (broker × asset-
 * class) → strategy, which is 1:1:
 *
 *   Ichimoku Equity  · T212 · US Equity   (positions from T212)
 *   Ichimoku FX      · IG   · FX          (FX deals from IG)
 *   Intraday EOD-flat· IG   · US Equity   (equity positions from IG)
 *
 * Broker is the golden source: positions + unrealised P&L attribute cleanly.
 * Cash is account-level (IG shared between FX + intraday) so per-desk "cash"
 * is the configured allocation, not segregated broker cash.
 *
 * T212 positions are passed in (KpiStrip/PositionCharts already use them);
 * IG + OMS are fetched here. Click a desk to expand its positions.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";
import type { T212PosResp } from "../../types/cockpit";
import { bareSymbol, exchangeOf, prettySymbol, productOf } from "../../util/brokerSymbols";
import { ownerFor } from "../../util/strategyMeta";

type IGPosResp = Awaited<ReturnType<typeof api.igPositions>>;
type OmsPositions = Awaited<ReturnType<typeof api.omsPositions>>;

const UP = "#1fc16b";
const DOWN = "#ef4444";
const AMBER = "#f59e0b";

type DeskDef = {
  id: string; label: string; broker: string; omsBroker: string;
  assetClass: "Equity" | "FX"; source: "t212" | "ig"; capitalUsd: number;
};
// capitalUsd MUST mirror each daemon's --capital-usd (the launchd plists):
// ichimoku_equity=50000, ichimoku_fx_mr=50000, intraday_flat=50000. It was
// 100_000 for equity here while the daemon ran --capital-usd 50000, so the
// cockpit showed "Alloc $100k" on a ~$50k T212 account. TODO: source this
// from a config endpoint instead of mirroring by hand (no-hardcoding).
const DESKS: DeskDef[] = [
  { id: "ichimoku_equity", label: "Ichimoku Equity", broker: "T212", omsBroker: "T212_DEMO", assetClass: "Equity", source: "t212", capitalUsd: 50_000 },
  { id: "ichimoku_fx_mr",  label: "Ichimoku FX",     broker: "IG",   omsBroker: "IG_DEMO",   assetClass: "FX",     source: "ig",   capitalUsd: 50_000 },
  { id: "intraday_flat",   label: "Intraday EOD-flat", broker: "IG", omsBroker: "IG_DEMO",   assetClass: "Equity", source: "ig",   capitalUsd: 50_000 },
];

// Spot FX 24/5; US equities ~13:30–20:00 UTC weekdays (DST-summer default).
function marketOpen(assetClass: "Equity" | "FX"): boolean {
  const now = new Date();
  const wd = now.getUTCDay(); // 0 Sun … 6 Sat
  const h = now.getUTCHours();
  const m = now.getUTCMinutes();
  if (assetClass === "FX") {
    if (wd === 6) return false;
    if (wd === 0) return h >= 21;
    if (wd === 5) return h < 21;
    return true;
  }
  // US equity
  if (wd === 0 || wd === 6) return false;
  const mins = h * 60 + m;
  return mins >= 13 * 60 + 30 && mins < 20 * 60;
}

type Pos = { symbol: string; qty: number; avg: number | null; now: number | null; unrlAbs: number | null; unrlPct: number | null };

export function StrategyDesks({
  positions, onHide,
}: {
  positions: T212PosResp | null;
  onHide: () => void;
}) {
  const [ig, setIg] = useState<IGPosResp | null>(null);
  const [oms, setOms] = useState<OmsPositions | null>(null);
  const [orders, setOrders] = useState<Awaited<ReturnType<typeof api.omsOrders>>["orders"]>([]);
  const [openDesk, setOpenDesk] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setIg(await api.igPositions()); } catch { /* keep */ }
    try { setOms(await api.omsPositions()); } catch { /* keep */ }
    // Waiting orders (signals queued at the broker boundary) — so a desk
    // with orders held for the open reads "N waiting", not "Flat".
    try { setOrders((await api.omsOrders(undefined, 300)).orders); } catch { /* keep */ }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const omsNet = (broker: string, sym: string): number | null => {
    if (!oms) return null;
    const rows = oms.positions.filter(
      (p) => p.broker?.toUpperCase() === broker.toUpperCase() && bareSymbol(p.symbol) === sym);
    return rows.length ? rows.reduce((n, p) => n + p.quantity, 0) : 0;
  };

  // Resolve each desk's positions from its broker (golden source).
  function deskPositions(d: DeskDef): Pos[] {
    if (d.source === "t212") {
      if (!positions?.enabled) return [];
      return positions.positions.map((p) => ({
        symbol: p.ticker, qty: p.quantity, avg: p.averagePricePaid,
        now: p.currentPrice, unrlAbs: p.unrealisedAbs, unrlPct: p.unrealisedPct,
      }));
    }
    // IG, filtered by asset class
    if (!ig?.enabled) return [];
    return ig.positions
      // Strict product match — previously "Equity" swallowed anything
      // non-FX, so IG options (OD.D.* weekly EUR CALL/PUT) showed up under
      // the equity desk. Options/futures now classify correctly and stay
      // off the strategy desks (no desk trades them).
      .filter((p) => productOf(p.ticker) === d.assetClass)
      // Same uniform shape as the T212 branch — the IG endpoint now computes
      // currentPrice + unrealised from broker data, so FX/IG desks show P&L too.
      .map((p) => ({
        symbol: p.ticker, qty: p.quantity, avg: p.averagePricePaid ?? null,
        now: p.currentPrice ?? null, unrlAbs: p.unrealisedAbs ?? null, unrlPct: p.unrealisedPct ?? null,
      }));
  }

  const desks = DESKS.map((d) => {
    // Net per pair/symbol for display. IG opens a separate DEAL per order
    // (no auto-netting), so a pair like EUR/USD can have 12 stacked deals
    // (+1, -1.7, -0.9 …) whose real position is the sum (-3.3). Show one
    // netted row per symbol — the individual deals live in the FX panel.
    const pos = netPositionsByBare(deskPositions(d));
    const unrl = pos.reduce((n, p) => n + (p.unrlAbs ?? 0), 0);
    const hasPnl = pos.some((p) => p.unrlAbs != null);
    // reconcile vs OMS by net per bare symbol
    const driftSyms: string[] = [];
    const netByBare = new Map<string, number>();
    for (const p of pos) netByBare.set(bareSymbol(p.symbol), (netByBare.get(bareSymbol(p.symbol)) ?? 0) + p.qty);
    for (const [bare, q] of netByBare) {
      const o = omsNet(d.omsBroker, bare);
      if (o != null && Math.round(o) !== Math.round(q)) driftSyms.push(bare);
    }
    const connected = d.source === "t212" ? !!positions?.enabled : !!ig?.enabled;
    // Waiting orders for this desk = strategy orders still at the broker
    // boundary (queued / held for the open). Match the desk's strategy id
    // (intraday_flat also owns "intraday-*" per-symbol ids).
    const waiting = orders.filter((o) => {
      if (o.state !== "PENDING_APPROVAL" && o.state !== "SUBMITTED" && o.state !== "WORKING") return false;
      const sid = o.strategyId || "";
      return sid === d.id || sid.startsWith(d.id)
        || (d.id === "intraday_flat" && sid.startsWith("intraday"));
    }).length;
    return { d, pos, unrl, hasPnl, driftSyms, connected, waiting, open: marketOpen(d.assetClass) };
  });

  const totalUnrl = desks.reduce((n, x) => n + (x.hasPnl ? x.unrl : 0), 0);
  const totalPos = desks.reduce((n, x) => n + x.pos.length, 0);

  return (
    <CockpitCard id="desks" title="Strategy desks" badge={totalPos || undefined} fullWidth onHide={onHide}>
      {/* Portfolio strip */}
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "baseline", paddingBottom: 10, marginBottom: 10, borderBottom: "1px solid var(--border)" }}>
        <Metric label="Unrealised (equity desks)" value={fmtSigned(totalUnrl)} colour={totalUnrl >= 0 ? UP : DOWN} big />
        <Metric label="Open positions" value={String(totalPos)} />
        <Metric label="Desks" value={`${desks.filter((x) => x.connected).length}/${desks.length} live`} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))", gap: 12 }}>
        {desks.map((x) => (
          <DeskCard key={x.d.id} x={x} expanded={openDesk === x.d.id}
            onToggle={() => setOpenDesk(openDesk === x.d.id ? null : x.d.id)}
            omsNet={omsNet} />
        ))}
      </div>
      <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
        Broker is the source of truth; ⚠ = OMS drift. Per-desk cash = configured allocation (IG cash is shared).
        {" · "}<Link to="/oms" style={{ color: "var(--text-muted)" }}>OMS →</Link>
        {" · "}<Link to="/portfolio" style={{ color: "var(--text-muted)" }}>Portfolio →</Link>
      </div>
    </CockpitCard>
  );
}

function DeskCard({ x, expanded, onToggle, omsNet }: {
  x: { d: DeskDef; pos: Pos[]; unrl: number; hasPnl: boolean; driftSyms: string[]; connected: boolean; waiting: number; open: boolean };
  expanded: boolean; onToggle: () => void; omsNet: (b: string, s: string) => number | null;
}) {
  const { d, pos, unrl, hasPnl, driftSyms, connected, waiting, open } = x;
  const movers = [...pos].filter((p) => p.unrlAbs != null).sort((a, b) => (b.unrlAbs ?? 0) - (a.unrlAbs ?? 0));
  const best = movers[0], worst = movers[movers.length - 1];
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px", background: "rgba(0,0,0,0.10)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13, color: "var(--text)" }}>{d.label}</strong>
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
          {ownerFor(d.id)} · {d.broker} · {d.assetClass}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 9, color: open ? UP : "var(--text-muted)", fontWeight: 700 }}>
          {connected ? (open ? "● live" : "○ closed") : "— off"}
        </span>
      </div>
      <div style={{ display: "flex", gap: 14, alignItems: "baseline", marginTop: 8, flexWrap: "wrap" }}>
        {hasPnl
          ? <Metric label="Unrealised" value={fmtSigned(unrl)} colour={unrl >= 0 ? UP : DOWN} big />
          : <Metric label="Exposure" value={`${pos.length} ${d.assetClass === "FX" ? "net" : "pos"}`} big />}
        <Metric label="Positions" value={String(pos.length)} />
        <Metric label="Alloc" value={`$${(d.capitalUsd / 1000).toFixed(0)}k`} />
        {waiting > 0 && (
          <span style={{ fontSize: 10, color: "#f59e0b", fontWeight: 700 }} title="Strategy orders queued at the broker (held for the open / awaiting approval)">
            ⏳ {waiting} waiting
          </span>
        )}
        {driftSyms.length > 0
          ? <span style={{ fontSize: 10, color: AMBER, fontWeight: 700 }} title={`OMS drift: ${driftSyms.join(", ")}`}>⚠ {driftSyms.length} drift</span>
          : connected && <span style={{ fontSize: 10, color: UP, fontWeight: 700 }}>✓ reconciled</span>}
      </div>
      {!connected ? (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
          {d.broker} not connected{d.id === "intraday_flat" ? " — trades the US session once IG epics are set." : "."}
        </div>
      ) : pos.length === 0 ? (
        <div style={{ fontSize: 11, color: waiting > 0 ? "#f59e0b" : "var(--text-muted)", marginTop: 8 }}>
          {waiting > 0
            ? `Flat — ${waiting} order${waiting === 1 ? "" : "s"} queued (held for the open / awaiting approval).`
            : "Flat — no open positions."}
        </div>
      ) : (
        <>
          {hasPnl && best && worst && (
            <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 6 }}>
              best <strong>{prettySymbol(best.symbol)} {fmtSigned(best.unrlAbs ?? 0)}</strong>
              {best !== worst && <> · worst <strong>{prettySymbol(worst.symbol)} {fmtSigned(worst.unrlAbs ?? 0)}</strong></>}
            </div>
          )}
          <button type="button" onClick={onToggle} style={{ marginTop: 8, fontSize: 11, background: "transparent", border: "none", color: "var(--text-dim)", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
            {expanded ? "Hide" : "Show"} positions
          </button>
          {expanded && (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, marginTop: 6 }}>
              <thead><tr style={{ color: "var(--text-dim)" }}>
                <th style={th}>Symbol</th><th style={rTh}>Qty</th><th style={rTh}>{hasPnl ? "P&L" : "Side"}</th><th style={rTh}>OMS</th>
              </tr></thead>
              <tbody>
                {pos.map((p, i) => {
                  const o = omsNet(d.omsBroker, bareSymbol(p.symbol));
                  const drift = o != null && Math.round(o) !== Math.round(p.qty);
                  return (
                    <tr key={`${p.symbol}-${i}`} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={td} title={p.symbol}>
                        {prettySymbol(p.symbol)}
                        {exchangeOf(p.symbol) && (
                          <span style={{ marginLeft: 6, fontSize: 9, padding: "1px 4px", borderRadius: 3,
                            background: "var(--border)", color: "var(--text-muted)", letterSpacing: "0.04em" }}>
                            {exchangeOf(p.symbol)}
                          </span>
                        )}
                      </td>
                      <td style={numTd}>{fmtQty(p.qty)}</td>
                      <td style={{ ...numTd, color: hasPnl ? ((p.unrlAbs ?? 0) >= 0 ? UP : DOWN) : (p.qty >= 0 ? UP : DOWN) }}>
                        {hasPnl ? fmtSigned(p.unrlAbs ?? 0) : (p.qty >= 0 ? "LONG" : "SHORT")}
                      </td>
                      <td style={{ ...numTd, color: drift ? AMBER : "var(--text-muted)" }}>{o == null ? "—" : drift ? `⚠ ${fmtQty(o)}` : fmtQty(o)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value, colour, big }: { label: string; value: string; colour?: string; big?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      <div style={{ fontSize: big ? 17 : 13, fontWeight: big ? 700 : 500, fontFamily: "monospace", color: colour ?? "var(--text)" }}>{value}</div>
    </div>
  );
}

function fmtSigned(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
}

/** Strip floating-point noise from a quantity for display (e.g. summing OMS
 *  rows yields 1.0000000000000009). Rounds to 6 dp and drops trailing zeros,
 *  so whole shares show clean (1) while real fractional shares (6.7022) and
 *  FX minis (-0.7) survive. */
function fmtQty(n: number): number {
  return Number(n.toFixed(6));
}

/** Net stacked deals into one row per bare symbol (sum qty + P&L). IG opens
 * a separate deal per order, so a pair shows many rows whose real position
 * is the sum; T212 is already netted so this is a no-op there. Net-zero
 * (fully offset) positions are dropped. unrlAbs stays null when no input
 * carried P&L (so the desk's hasPnl detection isn't fooled into 0). */
function netPositionsByBare(rows: Pos[]): Pos[] {
  const m = new Map<string, Pos>();
  for (const p of rows) {
    const k = bareSymbol(p.symbol);
    const ex = m.get(k);
    if (!ex) { m.set(k, { ...p }); continue; }
    ex.qty += p.qty;
    if (p.unrlAbs != null) ex.unrlAbs = (ex.unrlAbs ?? 0) + p.unrlAbs;
  }
  return [...m.values()].filter((p) => Math.abs(p.qty) > 1e-9);
}

const th: React.CSSProperties = { textAlign: "left", padding: "3px 6px", fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em" };
const rTh: React.CSSProperties = { ...th, textAlign: "right" };
const td: React.CSSProperties = { padding: "3px 6px" };
const numTd: React.CSSProperties = { ...td, textAlign: "right", fontFamily: "monospace" };
