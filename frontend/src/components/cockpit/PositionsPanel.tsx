/**
 * PositionsPanel — the cockpit's whole-book view, segregated BY BROKER,
 * then BY PRODUCT, with full reconciliation against each broker account.
 *
 * Why this shape: multiple strategies post to different brokers
 * (ichimoku_equity → T212, ichimoku_fx_mr → IG, …). The trader needs a
 * complete picture but cleanly segregated, and — because the BROKER is
 * the golden source of truth (OMS is audit-only) — every account is
 * reconciled: we compare the broker's actual net position per symbol
 * against what the OMS thinks, and HIGHLIGHT any difference (drift).
 *
 * Layout:
 *   T212 (demo) ─ Equity        ✓ reconciled / ⚠ N drift
 *   IG (demo)   ─ FX            ✓ / ⚠   [Flatten]
 *               ─ Equity
 *
 * The Flatten action (FX) closes/nets stacked IG deals — undoing the
 * duplicate-order accumulation — behind a confirm.
 *
 * Data is aggregated client-side from the existing per-broker endpoints
 * (T212 positions are passed in as a prop because KpiStrip/PositionCharts
 * already consume them; IG + OMS are fetched here). Adding a broker later
 * = add a fetch + an Account block.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";
import type { T212PosResp } from "../../types/cockpit";
import { bareSymbol, prettySymbol, productOf } from "../../util/brokerSymbols";

type IGPosResp = Awaited<ReturnType<typeof api.igPositions>>;
type OmsPositions = Awaited<ReturnType<typeof api.omsPositions>>;

const UP = "#1fc16b";
const DOWN = "#ef4444";
const AMBER = "#f59e0b";

const th: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", fontWeight: 600,
  fontSize: 11, textTransform: "uppercase", letterSpacing: "0.04em",
};
const rTh: React.CSSProperties = { ...th, textAlign: "right" };
const td: React.CSSProperties = { padding: "4px 8px" };
const numTd: React.CSSProperties = { ...td, textAlign: "right", fontFamily: "monospace" };
const tableStyle: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 12 };
const muted: React.CSSProperties = { fontSize: 12, color: "var(--text-muted)" };

export function PositionsPanel({
  positions,
  posErr,
  account,
  onHide,
  onSyncOms,
  showEquity = true,
  showFx = true,
}: {
  positions: T212PosResp | null;
  posErr: string | null;
  account: "demo" | "live";
  onHide: (id: string) => void;
  showEquity?: boolean;
  showFx?: boolean;
  /** Sync OMS from broker for the given OMS broker label (writes audited
   * reconcile adjustments so OMS matches the broker). When omitted the
   * per-account "Sync OMS ← broker" button is hidden. */
  onSyncOms?: (broker: string) => Promise<{ adjusted: number }>;
}) {
  const [ig, setIg] = useState<IGPosResp | null>(null);
  const [igErr, setIgErr] = useState<string | null>(null);
  const [oms, setOms] = useState<OmsPositions | null>(null);
  const [flattening, setFlattening] = useState(false);
  const [flattenMsg, setFlattenMsg] = useState<string | null>(null);
  const [showDeals, setShowDeals] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const loadBroker = useCallback(async () => {
    try {
      const d = await api.igPositions();
      setIg(d);
      setIgErr(null);
    } catch (e) {
      setIgErr(String(e));
    }
    try {
      setOms(await api.omsPositions());
    } catch {
      setOms(null);
    }
  }, []);

  useEffect(() => {
    void loadBroker();
    const t = setInterval(loadBroker, 30_000);
    return () => clearInterval(t);
  }, [loadBroker]);

  const doSync = useCallback(async (broker: string) => {
    if (!onSyncOms) return;
    if (!window.confirm(
      `Overwrite OMS for ${broker} with the broker's actual positions?\n\n`
      + `The broker is the source of truth; this writes audited "RECONCILE" `
      + `adjustments so the OMS net matches. It does NOT place real orders.`,
    )) return;
    setSyncMsg(`Syncing ${broker}…`);
    try {
      const r = await onSyncOms(broker);
      setSyncMsg(`Synced ${broker}: ${r.adjusted} adjustment(s).`);
      await loadBroker();
    } catch (e) {
      setSyncMsg(`Sync failed: ${e}`);
    }
  }, [onSyncOms, loadBroker]);

  // OMS net per (broker, bareSymbol) — the system's view, for reconcile.
  const omsNet = (brokerLabel: string, sym: string): number | null => {
    if (!oms) return null;
    const rows = oms.positions.filter(
      (p) => p.broker?.toUpperCase() === brokerLabel.toUpperCase()
        && bareSymbol(p.symbol) === sym,
    );
    if (rows.length === 0) return 0;
    return rows.reduce((n, p) => n + p.quantity, 0);
  };

  const flatten = useCallback(async (opts: { symbol?: string; dealId?: string; label: string }) => {
    if (!window.confirm(`Flatten ${opts.label} on IG? This closes at market (rejected if the market is closed).`)) return;
    setFlattening(true);
    setFlattenMsg(null);
    try {
      const r = await api.flattenIg({ symbol: opts.symbol, dealId: opts.dealId });
      const firstErr = r.details.find((d) => !d.ok)?.error;
      setFlattenMsg(
        r.closed === r.requested
          ? `Closed ${r.closed}/${r.requested} deal(s).`
          : `Closed ${r.closed}/${r.requested}; ${r.failed} could not close${firstErr ? ` — ${firstErr}` : ""}.`,
      );
      await loadBroker();
    } catch (e) {
      setFlattenMsg(`Flatten failed: ${e}`);
    } finally {
      setFlattening(false);
    }
  }, [loadBroker]);

  // ── Build the IG product groups (FX / Equity) from the deal list ──
  const igFx = (ig?.positions ?? []).filter((p) => productOf(p.ticker) === "FX");
  const igEq = (ig?.positions ?? []).filter((p) => productOf(p.ticker) !== "FX");

  const equityCount = positions?.enabled ? positions.positionCount : 0;

  const t212OmsBroker = `T212_${account.toUpperCase()}`;
  return (
    <>
      {/* ════ Card 1 — EQUITY (across brokers) ════ */}
      {showEquity && (
      <CockpitCard
        id="positions-equity"
        title="Equity positions"
        badge={(equityCount + igEq.length) || undefined}
        onHide={() => onHide("positions-equity")}
      >
        <Account
          label={`T212 · ${account}`}
          reconciled={positions?.enabled ? reconcileT212(positions, t212OmsBroker, omsNet) : null}
          first
          onSync={onSyncOms ? () => doSync(t212OmsBroker) : undefined}
        >
          <ProductSection
            title="Equity"
            loading={!positions}
            error={posErr}
            connected={!!positions?.enabled}
            empty={equityCount === 0}
            notConnected={`T212 ${account} not connected.`}
            emptyText={`No open equity positions in T212 ${account}.`}
          >
            <table style={tableStyle}>
              <thead>
                <tr style={{ color: "var(--text-dim)" }}>
                  <th style={th}>Ticker</th><th style={rTh}>Qty</th><th style={rTh}>Avg</th>
                  <th style={rTh}>Now</th><th style={rTh}>P&L %</th><th style={rTh}>P&L</th><th style={rTh}>OMS</th>
                </tr>
              </thead>
              <tbody>
                {positions?.positions.map((p) => {
                  const o = omsNet(t212OmsBroker, bareSymbol(p.ticker));
                  return (
                    <tr key={p.ticker} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={td}>{prettySymbol(p.ticker)}</td>
                      <td style={numTd}>{p.quantity}</td>
                      <td style={numTd}>{p.averagePricePaid?.toFixed(2) ?? "—"}</td>
                      <td style={numTd}>{p.currentPrice?.toFixed(2) ?? "—"}</td>
                      <td style={{ ...numTd, color: (p.unrealisedPct ?? 0) >= 0 ? UP : DOWN }}>
                        {p.unrealisedPct != null ? `${p.unrealisedPct >= 0 ? "+" : ""}${p.unrealisedPct.toFixed(2)}%` : "—"}
                      </td>
                      <td style={{ ...numTd, color: (p.unrealisedAbs ?? 0) >= 0 ? UP : DOWN }}>
                        {p.unrealisedAbs != null ? `${p.unrealisedAbs >= 0 ? "+" : ""}${p.unrealisedAbs.toFixed(2)}` : "—"}
                      </td>
                      <DriftCell broker={p.quantity} oms={o} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </ProductSection>
        </Account>

        {igEq.length > 0 && (
          <Account label={`IG · ${ig?.mode ?? "?"}`} reconciled={null}>
            <ProductSection title="Equity" loading={false} error={null} connected empty={false} notConnected="" emptyText="">
              <table style={tableStyle}>
                <thead>
                  <tr style={{ color: "var(--text-dim)" }}>
                    <th style={th}>Instrument</th><th style={rTh}>Qty</th><th style={rTh}>Entry</th><th style={rTh}>Side</th>
                  </tr>
                </thead>
                <tbody>
                  {igEq.map((p, i) => (
                    <tr key={p.dealId ?? `${p.ticker}-${i}`} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={td} title={p.ticker}>{p.instrumentName || p.ticker}</td>
                      <td style={numTd}>{Math.abs(p.quantity)}</td>
                      <td style={numTd}>{p.averagePricePaid?.toFixed(2) ?? "—"}</td>
                      <td style={{ ...numTd, color: p.quantity >= 0 ? UP : DOWN }}>{p.quantity >= 0 ? "LONG" : "SHORT"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ProductSection>
          </Account>
        )}
        {syncMsg && <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 8 }}>{syncMsg}</div>}
        <ReconNote />
      </CockpitCard>
      )}

      {/* ════ Card 2 — FX (IG) ════ */}
      {showFx && (
      <CockpitCard
        id="positions-fx"
        title="FX positions"
        badge={igFx.length || undefined}
        onHide={() => onHide("positions-fx")}
      >
        <Account
          label={`IG · ${ig?.mode ?? "?"}`}
          reconciled={ig?.enabled ? reconcileIg(ig, omsNet) : null}
          first
          onSync={ig?.enabled && onSyncOms ? () => doSync(ig.mode) : undefined}
        >
          <ProductSection
            title="FX"
            loading={!ig}
            error={igErr ?? ig?.error ?? null}
            connected={!!ig?.enabled}
            empty={igFx.length === 0}
            notConnected="IG not connected."
            emptyText="No open FX positions in IG."
            action={igFx.length > 0 ? (
              <button type="button" onClick={() => flatten({ label: "all open FX deals" })} disabled={flattening} style={flattenBtn}>
                {flattening ? "Flattening…" : "Flatten all FX"}
              </button>
            ) : undefined}
          >
            {flattenMsg && <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 6 }}>{flattenMsg}</div>}
            {/* Lead with the NET exposure per pair — the real position
                under the (often many) stacked deals. */}
            <NetByPair positions={igFx} prominent />
            {igFx.length > 0 && (
              <button type="button" onClick={() => setShowDeals((s) => !s)}
                style={{ marginTop: 8, fontSize: 11, background: "transparent", border: "none",
                  color: "var(--text-dim)", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
                {showDeals ? "Hide" : "Show"} {igFx.length} individual deal{igFx.length === 1 ? "" : "s"}
              </button>
            )}
            {showDeals && (
              <table style={{ ...tableStyle, marginTop: 6 }}>
                <thead>
                  <tr style={{ color: "var(--text-dim)" }}>
                    <th style={th}>Instrument</th><th style={rTh}>Qty</th><th style={rTh}>Entry</th>
                    <th style={rTh}>Side</th><th style={rTh}></th>
                  </tr>
                </thead>
                <tbody>
                  {igFx.map((p, i) => (
                    <tr key={p.dealId ?? `${p.ticker}-${i}`} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={td} title={p.ticker}>{p.instrumentName || prettySymbol(p.ticker)}</td>
                      <td style={numTd}>{Math.abs(p.quantity)}</td>
                      <td style={numTd}>{p.averagePricePaid?.toFixed(4) ?? "—"}</td>
                      <td style={{ ...numTd, color: p.quantity >= 0 ? UP : DOWN }}>{p.quantity >= 0 ? "LONG" : "SHORT"}</td>
                      <td style={{ ...numTd }}>
                        <button type="button"
                          onClick={() => flatten(p.dealId
                            ? { dealId: p.dealId, label: `this ${prettySymbol(p.ticker)} deal` }
                            : { symbol: bareSymbol(p.ticker), label: prettySymbol(p.ticker) })}
                          disabled={flattening}
                          style={{ ...flattenBtn, padding: "1px 7px", fontSize: 10 }}
                          title={p.dealId ? "Close this deal" : `Flatten ${bareSymbol(p.ticker)}`}>
                          close
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ProductSection>
        </Account>
        {syncMsg && <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 8 }}>{syncMsg}</div>}
        <ReconNote />
      </CockpitCard>
      )}
    </>
  );
}

function ReconNote() {
  return (
    <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-muted)" }}>
      Broker is the source of truth; OMS column = system's view (⚠ = drift).
      {" · "}<Link to="/portfolio" style={{ color: "var(--text-muted)" }}>Portfolio →</Link>
      {" · "}<Link to="/oms" style={{ color: "var(--text-muted)" }}>OMS →</Link>
    </div>
  );
}

// ── Reconciliation summaries (broker net vs OMS net per symbol) ──────
type Recon = { drift: number; symbols: string[] };

function reconcileT212(pos: T212PosResp, omsBroker: string, omsNet: (b: string, s: string) => number | null): Recon {
  const symbols: string[] = [];
  for (const p of pos.positions) {
    const bare = bareSymbol(p.ticker);
    const o = omsNet(omsBroker, bare);
    if (o != null && Math.round(o) !== Math.round(p.quantity)) symbols.push(bare);
  }
  return { drift: symbols.length, symbols };
}

function reconcileIg(ig: IGPosResp, omsNet: (b: string, s: string) => number | null): Recon {
  // Net the (possibly stacked) deals per pair, then compare to OMS.
  const net = new Map<string, number>();
  for (const p of ig.positions) {
    const bare = bareSymbol(p.ticker);
    net.set(bare, (net.get(bare) ?? 0) + p.quantity);
  }
  const symbols: string[] = [];
  for (const [bare, q] of net) {
    const o = omsNet(ig.mode, bare);
    if (o != null && Math.round(o) !== Math.round(q)) symbols.push(bare);
  }
  return { drift: symbols.length, symbols };
}

function Account({ label, reconciled, first, onSync, children }: {
  label: string; reconciled: Recon | null; first?: boolean;
  onSync?: () => void; children: React.ReactNode;
}) {
  const drift = reconciled && reconciled.drift > 0;
  const shown = reconciled?.symbols.slice(0, 6) ?? [];
  const moreCount = (reconciled?.symbols.length ?? 0) - shown.length;
  return (
    <div style={first ? { marginBottom: 6 } : { marginTop: 16, paddingTop: 12, borderTop: "2px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text)" }}>{label}</span>
        {reconciled && (
          reconciled.drift === 0 ? (
            <span style={{ fontSize: 10, color: UP, fontWeight: 700 }}>✓ reconciled</span>
          ) : (
            <span style={{ fontSize: 10, color: AMBER, fontWeight: 700 }}
              title={`OMS disagrees with broker on: ${reconciled.symbols.join(", ")}`}>
              ⚠ {reconciled.drift} drift ({shown.join(", ")}{moreCount > 0 ? ` +${moreCount}` : ""})
            </span>
          )
        )}
        {drift && onSync && (
          <button type="button" onClick={onSync} title="Overwrite OMS with the broker's actual positions (broker is the golden source)"
            style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, cursor: "pointer",
              border: `1px solid ${AMBER}`, background: `${AMBER}14`, color: AMBER }}>
            Sync OMS ← broker
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

function ProductSection({ title, loading, error, connected, empty, notConnected, emptyText, action, children }: {
  title: string; loading: boolean; error: string | null; connected: boolean; empty: boolean;
  notConnected: string; emptyText: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text-dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>{title}</span>
        {action && <span style={{ marginLeft: "auto" }}>{action}</span>}
      </div>
      {error ? <span style={{ fontSize: 12, color: DOWN }}>fetch failed: {error}</span>
        : loading ? <span style={muted}>Loading…</span>
        : !connected ? <span style={muted}>{notConnected}</span>
        : empty ? <span style={muted}>{emptyText}</span>
        : children}
    </div>
  );
}

function DriftCell({ broker, oms }: { broker: number; oms: number | null }) {
  if (oms == null) return <td style={{ ...numTd, color: "var(--text-muted)" }}>—</td>;
  const drift = Math.round(oms) !== Math.round(broker);
  return (
    <td style={{ ...numTd, color: drift ? AMBER : "var(--text-muted)" }} title={drift ? "OMS disagrees with broker" : "matches broker"}>
      {drift ? `⚠ ${oms}` : oms}
    </td>
  );
}

function NetByPair({ positions, prominent }: { positions: IGPosResp["positions"]; prominent?: boolean }) {
  const net = new Map<string, number>();
  for (const p of positions) {
    const bare = bareSymbol(p.ticker);
    net.set(bare, (net.get(bare) ?? 0) + p.quantity);
  }
  const rows = [...net.entries()].filter(([, q]) => Math.abs(q) > 1e-9);
  if (rows.length === 0) return null;
  if (prominent) {
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "baseline" }}>
        {rows.map(([s, q]) => (
          <span key={s} style={{ fontSize: 13 }}>
            <strong style={{ color: "var(--text)" }}>{prettySymbol(s)}</strong>{" "}
            <strong style={{ color: q >= 0 ? UP : DOWN, fontSize: 15, fontFamily: "monospace" }}>
              {q >= 0 ? "+" : ""}{q.toFixed(1)}
            </strong>
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}> net</span>
          </span>
        ))}
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>across {positions.length} deal{positions.length === 1 ? "" : "s"}</span>
      </div>
    );
  }
  return (
    <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-dim)" }}>
      Net: {rows.map(([s, q]) => (
        <span key={s} style={{ marginRight: 10 }}>
          {prettySymbol(s)} <strong style={{ color: q >= 0 ? UP : DOWN }}>{q >= 0 ? "+" : ""}{q.toFixed(1)}</strong>
        </span>
      ))}
      <span style={{ color: "var(--text-muted)" }}>({positions.length} deals)</span>
    </div>
  );
}

const flattenBtn: React.CSSProperties = {
  fontSize: 11, padding: "3px 10px", borderRadius: 6,
  border: `1px solid ${DOWN}`, background: `${DOWN}14`, color: DOWN, cursor: "pointer",
};
