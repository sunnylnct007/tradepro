/**
 * DeskKpiStrip — the always-visible top strip of the cockpit: the handful of
 * numbers a trader needs at a glance, as compact chips, so the detail panels below
 * can be scanned on demand rather than all shouting at once. Part of the compact-UX
 * pass (KPI strip + 2-col grid). Each chip is a headline number; the full
 * derivation lives in the panel it summarises (hover there for the explainer).
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

type Pnl = Awaited<ReturnType<typeof api.pnlByStrategy>>["rows"][number];
type Audit = Awaited<ReturnType<typeof api.signalAudit>>["artifact"];

function money(n: number | null | undefined, ccy = "GBP"): string {
  if (n == null) return "—";
  const s = ccy === "GBP" ? "£" : ccy === "USD" ? "$" : "";
  return `${n < 0 ? "−" : ""}${s}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function Chip({ label, value, tone, sub }: { label: string; value: string; tone?: "bad" | "good" | "warn"; sub?: string }) {
  const color = tone === "bad" ? "#f85149" : tone === "good" ? "#3fb950" : tone === "warn" ? "#d29922" : "var(--text)";
  const bg = tone === "bad" ? "rgba(248,81,73,0.08)" : tone === "warn" ? "rgba(210,153,34,0.08)" : "rgba(255,255,255,0.03)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1, padding: "4px 10px", borderRadius: 6,
      background: bg, border: "1px solid #1b2233", minWidth: 92 }}>
      <span style={{ fontSize: 8.5, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 700, color }}>{value}</span>
      {sub && <span style={{ fontSize: 8.5, color: "var(--text-muted)" }}>{sub}</span>}
    </div>
  );
}

export function DeskKpiStrip() {
  const [rows, setRows] = useState<Pnl[]>([]);
  const [audit, setAudit] = useState<Audit | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, a] = await Promise.allSettled([api.pnlByStrategy(), api.signalAudit("ichimoku_equity")]);
      if (p.status === "fulfilled") setRows(p.value.rows);
      if (a.status === "fulfilled") setAudit(a.value.artifact);
    } catch { /* strip is best-effort; panels below carry the authoritative view */ }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  // THE HEADLINE NUMBERS ARE THE LIVE SLEEVES (28 Sep 2026).
  //
  // Two of the four chips were hardcoded to ichimoku_fx_mr and intraday_flat.
  // Both are RETIRED: FX has not traded in 35 days and lost 29.89 over 161
  // trades; intraday_flat is the largest realised loss on the desk at -3,019
  // and its lane is unloaded. So the top of the portfolio screen led with two
  // dead strategies while swing and momentum — the two actually placing orders
  // — appeared nowhere.
  //
  // Now driven by ACTIVITY, the same ledger-derived field the P&L card uses:
  // the sleeves that traded in the last 7 days get the chips, in order of how
  // much they have on. A sleeve that stops trading drops out on its own, and a
  // new one appears without anyone editing this file — which is the failure
  // these two hardcoded ids were.
  const live = rows
    .filter((r) => (r as any).activity === "active")
    .sort((a, b) => Math.abs((b.openPnl ?? 0)) - Math.abs((a.openPnl ?? 0)))
    .slice(0, 2);
  const nlvVsStart = audit?.pnl?.total_pnl ?? null;
  const exitsOverdue = audit?.counts?.exit_overdue ?? null;
  const blind = audit?.counts?.blind ?? null;

  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "stretch",
      padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 8, background: "rgba(255,255,255,0.015)" }}>
      <Chip label="NLV vs start" value={money(nlvVsStart, audit?.currency ?? "GBP")}
        sub={audit?.pnl?.total_pnl_pct != null ? `${audit.pnl.total_pnl_pct}%` : undefined}
        tone={nlvVsStart != null && nlvVsStart < 0 ? "bad" : nlvVsStart != null ? "good" : undefined} />
      <Chip label="Exits overdue" value={exitsOverdue != null ? String(exitsOverdue) : "—"}
        tone={exitsOverdue ? "bad" : exitsOverdue === 0 ? "good" : undefined}
        sub={blind ? `${blind} blind` : undefined} />
      {live.map((r) => (
        <Chip key={r.strategyId}
          // The id, shortened — "swing" and "momentum" read faster than the
          // full strategy id and there is no ambiguity with two sleeves.
          label={r.strategyId.replace(/_ibkr$/, "").replace("mean_reversion_", "")}
          value={money(r.openPnl, r.currency ?? "GBP")}
          tone={(r.openPnl ?? 0) < 0 ? "bad" : (r.openPnl ?? 0) > 0 ? "good" : undefined}
          sub="open" />
      ))}
      {live.length === 0 && (
        // NOT a blank space. No live sleeve is a fact worth stating — it means
        // nothing has traded in a week, which is either a quiet market or a
        // stopped lane, and the reader needs to know which question to ask.
        <Chip label="live sleeves" value="none"
          sub="nothing traded in 7 days" tone="bad" />
      )}
    </div>
  );
}
