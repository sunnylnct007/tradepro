/**
 * SignalAuditCard — the "are we running blind?" panel. Two truths the rest of the
 * desk hides, both caught by the trader on the live book:
 *
 *  1. EXITS NOT FIRING — held names whose STATEFUL Ichimoku signal already said
 *     SELL but are still held (STRL/STX/AMKR…). The signal is correct; the exit
 *     just never executed. Shown red with days-overdue + the P&L still bleeding.
 *
 *  2. HONEST P&L — anchored to NLV-vs-starting-capital, NOT unrealised-on-held.
 *     The held book can read ~flat while the ACCOUNT is down (the T212 control:
 *     held −£32 but NLV −£2,007, because realized churn/cost/FX losses hide).
 *     We show total, and split out realized+costs so it can't hide again.
 *
 * Fed by tradepro-signal-audit --push → /api/signal-audit/{strategy}/latest.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

type Audit = Awaited<ReturnType<typeof api.signalAudit>>["artifact"];
type State = "loading" | "ok" | "error";

const STRATEGIES = [
  { key: "ichimoku_equity", label: "Equity (T212)" },
  { key: "ichimoku_equity_ibkr", label: "Clone (IBKR)" },
];

function money(n: number | null | undefined, ccy: string): string {
  if (n == null) return "—";
  const s = ccy === "GBP" ? "£" : ccy === "USD" ? "$" : ccy === "EUR" ? "€" : "";
  return `${n < 0 ? "−" : ""}${s}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
const col = (n: number | null | undefined) =>
  n == null ? "var(--text-dim)" : n >= 0 ? "#3fb950" : "#f85149";

export function SignalAuditCard() {
  const [strategy, setStrategy] = useState(STRATEGIES[0].key);
  const [state, setState] = useState<State>("loading");
  const [a, setA] = useState<Audit | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState("loading");
    try {
      const d = await api.signalAudit(strategy);
      setA(d.artifact); setState("ok"); setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e)); setState("error");
    }
  }, [strategy]);
  useEffect(() => { void load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  const ccy = a?.currency ?? "GBP";
  const p = a?.pnl;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: "1px solid #1b2233" }}>
        <span style={{ fontWeight: 600 }}>Signal vs Position — exits &amp; honest P&amp;L</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 3 }}>
          {STRATEGIES.map((s) => (
            <button key={s.key} onClick={() => setStrategy(s.key)}
              style={{ fontSize: 9.5, padding: "1px 7px", borderRadius: 5,
                border: "1px solid #1b2233", cursor: "pointer",
                background: strategy === s.key ? "rgba(79,140,255,0.15)" : "rgba(255,255,255,0.03)",
                color: strategy === s.key ? "var(--accent, #4f8cff)" : "var(--text-dim)" }}>
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {state === "loading" && !a && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Auditing…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>
        No audit yet: {err}. Run <code>tradepro-signal-audit --push</code> on the worker.
      </div>}

      {a && p && (
        <div style={{ padding: "7px 10px" }}>
          {/* Honest P&L headline — NLV vs start, with the hidden realized loss split out */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px,1fr))", gap: 8 }}>
            <Stat label={`NLV vs start (${ccy})`} v={`${money(p.total_pnl, ccy)}`}
              sub={p.total_pnl_pct != null ? `${p.total_pnl_pct >= 0 ? "+" : ""}${p.total_pnl_pct}%` : undefined}
              color={col(p.total_pnl)} big />
            <Stat label="held (unrealised)" v={money(p.unrealised_on_held, ccy)} color={col(p.unrealised_on_held)}
              sub="what the desk showed" />
            <Stat label="realized + costs" v={money(p.realized_and_costs, ccy)} color={col(p.realized_and_costs)}
              sub="the hidden churn/fees/FX" />
            <Stat label="cash / invested" v={`${money(p.cash, ccy)} / ${money(p.invested_cost, ccy)}`} />
          </div>
          {p.realized_and_costs != null && p.realized_and_costs < -1 && (
            <div style={{ marginTop: 6, fontSize: 10, color: "#d29922", background: "rgba(210,153,34,0.08)", border: "1px solid #5a4a1a", borderRadius: 5, padding: "4px 7px" }}>
              ⚠ The desk's held-P&amp;L reads {money(p.unrealised_on_held, ccy)} (≈flat) but the account is{" "}
              <b>{money(p.total_pnl, ccy)}</b> — <b>{money(p.realized_and_costs, ccy)}</b> is realized churn / spread / FX
              the unrealised view hides. NLV-vs-start is the honest number.
            </div>
          )}

          {/* Exits not firing */}
          <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Exits not firing — signal says SELL, still held ({a.counts.exit_overdue})
          </div>
          {a.exit_overdue.length === 0 && (
            <div style={{ fontSize: 10.5, color: "#3fb950", padding: "3px 0" }}>None — every held name still signals HOLD. ✓</div>
          )}
          {a.exit_overdue.map((r) => (
            <div key={r.symbol ?? Math.random()} style={{ display: "grid", gridTemplateColumns: "60px 1fr 1fr auto", gap: 6, padding: "3px 0", borderTop: "1px solid #11161f", alignItems: "baseline", fontSize: 11 }}>
              <span style={{ fontFamily: "monospace", fontWeight: 700, color: "#f85149" }}>{r.symbol ?? "?"}</span>
              <span style={{ color: "var(--text-dim)", fontSize: 10 }}>{r.entry} → {r.price}</span>
              <span style={{ color: col(r.pnl_pct), fontWeight: 700 }}>{r.pnl_pct != null ? `${r.pnl_pct}%` : "—"}</span>
              <span style={{ color: "#d29922", fontSize: 9.5 }}>SELL {r.days_overdue != null ? `${r.days_overdue}d overdue` : "pending"}</span>
            </div>
          ))}

          {a.blind.length > 0 && (
            <div style={{ marginTop: 7, fontSize: 9.5, color: "#d29922" }}>
              👁 <b>Blind ({a.counts.blind})</b> — held but no bars to evaluate exits: {a.blind.filter(Boolean).join(", ")}. Harvest these (golden-source gap).
            </div>
          )}
          <div style={{ marginTop: 6, fontSize: 9, color: "var(--text-muted)", lineHeight: 1.5 }}>
            Signal = trader's stateful Ichimoku (exit when Close&lt;cloud_bottom OR tenkan&lt;kijun). <b>SELL-still-held = exit fired but never executed</b> (the orphan/exit-execution gap).
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, v, sub, color, big }: { label: string; v: string; sub?: string; color?: string; big?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 8.5, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: big ? 14 : 12, fontWeight: 700, color: color ?? "var(--text)" }}>{v}</div>
      {sub && <div style={{ fontSize: 8.5, color: "var(--text-muted)" }}>{sub}</div>}
    </div>
  );
}
