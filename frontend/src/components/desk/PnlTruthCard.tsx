/**
 * PnlTruthCard — the HONEST P&L picture per strategy, part of the
 * "operational + data truthfulness" clarity workstream.
 *
 * The desk elsewhere shows a single P&L number, which hides the truth: a
 * positive "total" can be propped up by UNREALISED marks (open positions that
 * can evaporate) — on the ICH clone, by ~15 orphaned high-beta holdings it
 * isn't even managing. This card separates the trustworthy number (REALISED =
 * booked closed trades) from the soft one (OPEN = unrealised mark-to-market),
 * and flags when Open is soft. No fabricated positives (feedback_no_false_positives).
 *
 *   Realised (booked) · Open (unrealised) · Net · win-rate · trades
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { brokerPortal } from "../../util/brokerLinks";

type Row = Awaited<ReturnType<typeof api.pnlByStrategy>>["rows"][number];
type State = "loading" | "ok" | "error";

// Strategies whose OPEN P&L is currently SOFT (holds positions it isn't managing
// — the orphaned-positions bug). Until the wind-down engine fix lands, flag it.
const SOFT_OPEN = new Set(["ichimoku_equity_ibkr"]);

function money(n: number | null | undefined, ccy: string): string {
  if (n == null) return "—";
  const sym = ccy === "GBP" ? "£" : ccy === "USD" ? "$" : ccy === "EUR" ? "€" : "";
  return `${n < 0 ? "−" : ""}${sym}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
const col = (n: number | null | undefined) =>
  n == null ? "var(--text-dim)" : n >= 0 ? "#3fb950" : "#f85149";

export function PnlTruthCard() {
  const [state, setState] = useState<State>("loading");
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.pnlByStrategy();
      // Show strategies that have actually traded (realised or open or trades).
      const r = d.rows.filter((x) => x.trades > 0 || x.realisedLtd != null || x.openPnl != null);
      setRows(r); setState("ok"); setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e)); setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>P&amp;L — the honest split</span>
        <button onClick={() => void load()} title="refresh"
          style={{ marginLeft: "auto", fontSize: 10, padding: "1px 7px", borderRadius: 5, border: "1px solid #1b2233", background: "rgba(255,255,255,0.03)", color: "var(--text-dim)", cursor: "pointer" }}>↻</button>
      </div>

      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Loading…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>P&amp;L unavailable: {err} (EC2 may be asleep)</div>}

      {state === "ok" && (
        <div style={{ padding: "4px 6px 8px" }}>
          {/* header row */}
          <div style={{ display: "grid", gridTemplateColumns: "minmax(120px,1.4fr) 1fr 1fr 1fr auto", gap: 6, padding: "2px 6px", fontSize: 8.5, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            <span>strategy</span><span>realised (booked)</span><span>open (unrealised)</span><span>net</span><span>win</span>
          </div>
          {rows.map((r) => {
            const net = (r.realisedLtd ?? 0) + (r.openPnl ?? 0);
            const bothKnown = r.realisedLtd != null && r.openPnl != null;
            const soft = SOFT_OPEN.has(r.strategyId) && (r.openPnl ?? 0) > 0;
            // UNCONFIRMED: the whole book is OMS-simulated (no broker ack). Its
            // numbers are NOT broker-verified, so we mute them so nobody reads
            // them like the real, broker-confirmed rows (the whole point: you
            // can't compare the clone vs the T212 original on made-up fills).
            const unconf = r.unconfirmed;
            const numColor = (c: string) => (unconf ? "var(--text-dim)" : c);
            return (
              <div key={r.strategyId} title={unconf ? r.confirmation : undefined} style={{ display: "grid", gridTemplateColumns: "minmax(120px,1.4fr) 1fr 1fr 1fr auto", gap: 6, padding: "4px 6px", borderTop: "1px solid #11161f", alignItems: "baseline", fontSize: 11, opacity: unconf ? 0.72 : 1 }}>
                <span style={{ fontFamily: "monospace", fontSize: 10, color: "var(--text)" }}>
                  {r.strategyId}
                  {unconf && (
                    <span title={r.confirmation}
                      style={{ marginLeft: 5, fontSize: 8, fontWeight: 700, padding: "0px 4px", borderRadius: 4, background: "rgba(248,81,73,0.15)", border: "1px solid rgba(248,81,73,0.5)", color: "#f85149", letterSpacing: "0.03em", cursor: "help" }}>UNCONFIRMED</span>
                  )}
                  {(() => { const bp = brokerPortal(r.broker); return bp ? (
                    <a href={bp.url} target="_blank" rel="noopener noreferrer" title={`Open ${bp.label} for positions/fills/P&L detail`}
                       style={{ marginLeft: 5, fontSize: 8.5, color: "var(--accent, #4f8cff)", textDecoration: "none" }}>↗{r.broker.split("_")[0]}</a>
                  ) : null; })()}
                </span>
                <span style={{ color: numColor(col(r.realisedLtd)), fontWeight: unconf ? 400 : 700 }} title={unconf ? r.confirmation : "booked P&L on closed trades — the trustworthy number"}>{money(r.realisedLtd, r.currency)}{unconf && "*"}</span>
                <span style={{ color: numColor(col(r.openPnl)) }} title={unconf ? r.confirmation : "unrealised mark-to-market — moves with price, not booked"}>
                  {money(r.openPnl, r.currency)}{unconf && "*"}{soft && <span style={{ color: "#d29922", fontSize: 9 }} title="includes orphaned positions the strategy isn't managing — soft"> ⚠</span>}
                </span>
                <span style={{ color: bothKnown ? numColor(col(net)) : "var(--text-dim)" }}>{bothKnown ? `${money(net, r.currency)}${unconf ? "*" : ""}` : "—"}</span>
                <span style={{ color: "var(--text-muted)", fontSize: 10 }}>{r.winRatePct != null ? `${r.winRatePct.toFixed(0)}%` : "—"}·{r.trades}t</span>
              </div>
            );
          })}
          <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", padding: "0 6px", lineHeight: 1.5 }}>
            <b style={{ color: "#3fb950" }}>Realised</b> = booked closed trades (trust this). <b>Open</b> = unrealised mark-to-market — moves with price, not money in the bank.
            <span style={{ color: "#d29922" }}> ⚠ = Open is soft</span> (holds orphaned positions the strategy isn't managing → Net overstates the real result until wound down).
            {rows.some((r) => r.unconfirmed) && (
              <><br /><span style={{ color: "#f85149" }}>UNCONFIRMED / *</span> = every fill is OMS-simulated with <b>no broker order id</b> — these numbers are <b>not broker-verified</b> and are <b>not comparable</b> to the real broker-confirmed rows (e.g. the IBKR paper clones vs the T212 original). Fix in flight: route clone execution through the broker.</>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
