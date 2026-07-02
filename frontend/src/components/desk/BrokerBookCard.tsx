/**
 * BrokerBookCard — the BROKER-GOLDEN account truth, read straight from the
 * broker (IBKR/T212/IG) via /api/integrations/account-state. This is the
 * authoritative book (positions · avgCost=deployed · mark · marketValue ·
 * per-position unrealised P&L · NLV), NOT the OMS-computed view — so it reveals
 * what the OMS hides: SHORT positions (the clone is long/flat, so a short is a
 * BUG), corp-action splits, and stuck/unreconciled fills.
 *
 * The "central orchestrator, read from the broker" vision made concrete:
 * TradePro aggregates each broker's golden read into one cross-broker cockpit.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { brokerPortal } from "../../util/brokerLinks";

type Acct = Awaited<ReturnType<typeof api.accountState>>["accounts"][number];
type State = "loading" | "ok" | "error";

function money(n: number | null | undefined, ccy: string | null | undefined = "USD"): string {
  if (n == null) return "—";
  const s = ccy === "GBP" ? "£" : ccy === "USD" ? "$" : ccy === "EUR" ? "€" : "";
  return `${n < 0 ? "−" : ""}${s}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function BrokerBookCard() {
  const [state, setState] = useState<State>("loading");
  const [accts, setAccts] = useState<Acct[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.accountState();
      setAccts(d.accounts ?? []); setState("ok"); setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e)); setState("error");
    }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", fontSize: 11, borderBottom: state === "ok" ? "1px solid #1b2233" : "none" }}>
        <span style={{ fontWeight: 600 }}>Broker book — golden (read from broker)</span>
        <button onClick={() => void load()} title="refresh" style={{ marginLeft: "auto", fontSize: 10, padding: "1px 7px", borderRadius: 5, border: "1px solid #1b2233", background: "rgba(255,255,255,0.03)", color: "var(--text-dim)", cursor: "pointer" }}>↻</button>
      </div>
      {state === "loading" && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)" }}>Reading brokers…</div>}
      {state === "error" && <div style={{ padding: "8px 10px", fontSize: 11, color: "#f85149" }}>Unavailable: {err} (EC2 may be asleep)</div>}
      {state === "ok" && accts.length === 0 && <div style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>No broker account state pushed yet.</div>}

      {state === "ok" && accts.map((a) => {
        const pos = a.positions ?? [];
        const longs = pos.filter((p) => p.qty > 0);
        const shorts = pos.filter((p) => p.qty < 0);
        const deployed = longs.reduce((s, p) => s + (p.avgCost ?? 0) * p.qty, 0);
        const bp = brokerPortal(a.broker);
        return (
          <div key={a.broker + a.accountId} style={{ padding: "6px 10px", borderTop: "1px solid #11161f" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 700, fontSize: 11 }}>{a.broker}</span>
              <span style={{ fontSize: 9.5, color: "var(--text-muted)" }}>{a.accountId}</span>
              {bp && <a href={bp.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 9, color: "var(--accent, #4f8cff)", textDecoration: "none" }}>↗</a>}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(88px,1fr))", gap: 8, marginTop: 5 }}>
              <Stat label="NLV" v={money(a.netLiquidation, a.currency)} />
              <Stat label="deployed (long cost)" v={money(deployed, "USD")} />
              <Stat label="unrealised P&L" v={money(a.unrealisedPnl, a.currency)} good={a.unrealisedPnl != null ? a.unrealisedPnl >= 0 : undefined} />
              <Stat label="positions" v={`${longs.length} long${shorts.length ? ` · ${shorts.length} SHORT` : ""}`} good={shorts.length ? false : undefined} />
            </div>
            {shorts.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 10, color: "#f85149", background: "rgba(248,81,73,0.08)", border: "1px solid #5a2222", borderRadius: 5, padding: "4px 7px" }}>
                ⚠ <b>{shorts.length} SHORT position{shorts.length > 1 ? "s" : ""}</b> — the strategy is long/flat, a short is a BUG:{" "}
                {shorts.map((p) => `${p.symbol} ${p.qty}`).join(", ")} (net-short risk — investigate).
              </div>
            )}
          </div>
        );
      })}
      {state === "ok" && (
        <div style={{ padding: "4px 10px 8px", fontSize: 9, color: "var(--text-muted)" }}>
          Read straight from the broker (golden source) — the truth the OMS-computed view can miss.
        </div>
      )}
    </div>
  );
}

function Stat({ label, v, good }: { label: string; v: string; good?: boolean }) {
  const color = good === undefined ? "var(--text)" : good ? "#3fb950" : "#f85149";
  return (
    <div>
      <div style={{ fontSize: 8.5, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color }}>{v}</div>
    </div>
  );
}
