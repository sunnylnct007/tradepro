/**
 * PaperLedgerCard — "is this strategy making money?" answered from what the
 * broker confirmed, attributed by the placement record that caused it.
 *
 * Owner, 9 Nov 2026: "the paper trading execution and reconciliation is still
 * a mess. not a clean way of saying if a strategy is making money or not."
 * Ruling, 10 Nov: broker-confirmed fills by placement record; unattributed as
 * its own line.
 *
 * What this card deliberately does NOT show: open (unrealised) P&L. A mark is
 * not a result, and the previous card's net line let a flat held-P&L hide a
 * −4% account. Realised, from confirmed fills, is the only number here that is
 * money; everything else on a line is a caveat about how complete it is.
 *
 * Reads GET /api/pnl/ledger (PaperLedger.cs). PAPER ONLY by construction.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

type Ledger = Awaited<ReturnType<typeof api.paperLedger>>;
type Line = Ledger["strategies"][number];

const TONE = { ok: "#3fb950", bad: "#f85149", warn: "#d29922", dim: "var(--text-dim)", muted: "var(--text-muted)" };

function money(n: number | null | undefined, ccy: string): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sym = ccy === "GBP" ? "£" : ccy === "EUR" ? "€" : "$";
  return `${n < 0 ? "−" : ""}${sym}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = (Date.now() - Date.parse(iso)) / 864e5;
  if (d < 1) return "today";
  if (d < 2) return "yesterday";
  return `${Math.floor(d)}d ago`;
}

export function PaperLedgerCard() {
  const [data, setData] = useState<Ledger | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showQuiet, setShowQuiet] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api.paperLedger()); setErr(null); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 120_000); return () => clearInterval(t); }, [load]);

  const lines = data?.strategies ?? [];
  // Traded in the last 30 days above the fold; the rest counted, never hidden.
  const cut = Date.now() - 30 * 864e5;
  const recent = lines.filter((l) => l.lastFillAtUtc && Date.parse(l.lastFillAtUtc) >= cut);
  const quiet = lines.filter((l) => !recent.includes(l));
  const shown = showQuiet ? [...recent, ...quiet] : recent;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: "1px solid #1b2233" }}>
        <span style={{ fontWeight: 600 }}>P&amp;L ledger — what the broker confirmed</span>
        <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 999, border: `1px solid ${TONE.warn}`, color: TONE.warn, letterSpacing: "0.05em" }}>PAPER</span>
        {data && (
          <span style={{ color: TONE.muted }}>
            {data.confirmedFills} confirmed · {data.assumedFills} assumed · {data.unexplainedBrokerPositions ?? "?"} broker positions no order explains
          </span>
        )}
        <button onClick={() => void load()} title="refresh"
          style={{ marginLeft: "auto", fontSize: 10, padding: "1px 7px", borderRadius: 5, border: "1px solid #1b2233", background: "rgba(255,255,255,0.03)", color: TONE.dim, cursor: "pointer" }}>↻</button>
      </div>

      {err && <div style={{ padding: "8px 10px", fontSize: 11, color: TONE.bad }}>Ledger unavailable: {err}</div>}
      {data?.error && <div style={{ padding: "8px 10px", fontSize: 11, color: TONE.bad }}>Ledger read failed: {data.error}</div>}
      {data && !data.error && (
        <div style={{ padding: "4px 6px 8px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(150px,1.6fr) 1fr 1fr 0.7fr 1.6fr", gap: 6, padding: "2px 6px", fontSize: 8.5, color: TONE.muted, textTransform: "uppercase", letterSpacing: "0.04em" }}>
            <span>strategy · broker</span><span>realised (confirmed)</span><span>today</span><span>win</span><span>completeness</span>
          </div>
          {shown.length === 0 && (
            <div style={{ padding: "6px", fontSize: 11, color: TONE.muted }}>
              {lines.length === 0 ? "No confirmed fills in the OMS yet." : `Nothing traded in the last 30 days (${quiet.length} quiet sleeve${quiet.length === 1 ? "" : "s"} below).`}
            </div>
          )}
          {shown.map((l) => <Row key={`${l.key}-${l.broker}-${l.currency}`} l={l} />)}
          {quiet.length > 0 && (
            <div onClick={() => setShowQuiet((v) => !v)} style={{ fontSize: 10.5, color: TONE.dim, cursor: "pointer", padding: "6px 6px 0", userSelect: "none" }}>
              {showQuiet ? "▾ hide" : "▸ show"} {quiet.length} sleeve{quiet.length === 1 ? "" : "s"} with no fill in 30 days
            </div>
          )}

          {/* UNATTRIBUTED — its own block, never a peer row, never folded in. */}
          {data.unattributed.length > 0 && (
            <div style={{ marginTop: 8, borderTop: `1px dashed ${TONE.warn}66`, paddingTop: 6 }}>
              <div style={{ fontSize: 9, color: TONE.warn, textTransform: "uppercase", letterSpacing: "0.05em", padding: "0 6px 2px" }}>
                Unattributed — fills no placement record claims
              </div>
              {data.unattributed.map((l) => <Row key={`un-${l.broker}-${l.currency}`} l={l} unattributed />)}
            </div>
          )}

          <div style={{ fontSize: 10, color: TONE.muted, padding: "8px 6px 0", lineHeight: 1.45 }}>{data.basis}</div>
        </div>
      )}
    </div>
  );
}

function Row({ l, unattributed = false }: { l: Line; unattributed?: boolean }) {
  const hasMoney = l.confirmedFills > 0;
  const tone = !hasMoney ? TONE.muted : l.realisedLtd > 0 ? TONE.ok : l.realisedLtd < 0 ? TONE.bad : TONE.dim;
  const caveats: string[] = [];
  if (l.assumedFills > 0) caveats.push(`${l.assumedFills} assumed`);
  if (l.unmatchedSellQty > 0) caveats.push(`${l.unmatchedSellQty} sold w/o lot`);
  return (
    <div title={l.notes.join("\n")}
         style={{ display: "grid", gridTemplateColumns: "minmax(150px,1.6fr) 1fr 1fr 0.7fr 1.6fr", gap: 6, padding: "4px 6px", fontSize: 11.5, borderTop: "1px solid #141a26", alignItems: "baseline" }}>
      <span style={{ fontFamily: "var(--font-mono)", color: unattributed ? TONE.warn : "var(--text)" }}>
        {unattributed ? "— no strategy —" : l.key}
        <span style={{ color: TONE.muted, fontSize: 10 }}> · {l.broker}</span>
      </span>
      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: tone }}>
        {hasMoney ? money(l.realisedLtd, l.currency) : "no money"}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", color: l.realisedToday ? (l.realisedToday > 0 ? TONE.ok : TONE.bad) : TONE.muted }}>
        {hasMoney && l.realisedToday ? money(l.realisedToday, l.currency) : "—"}
      </span>
      <span style={{ fontFamily: "var(--font-mono)", color: TONE.dim }}>
        {l.winRatePct != null ? `${l.winRatePct.toFixed(0)}% / ${l.closedTrades}` : "—"}
      </span>
      <span style={{ fontSize: 10.5, color: caveats.length ? TONE.warn : TONE.muted }}>
        {l.confirmedFills} confirmed · {l.symbols} sym · last {ago(l.lastFillAtUtc)}
        {caveats.length ? ` · ${caveats.join(" · ")}` : ""}
      </span>
    </div>
  );
}
