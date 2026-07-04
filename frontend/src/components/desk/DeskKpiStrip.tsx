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

  const by = (id: string) => rows.find((r) => r.strategyId === id);
  const fx = by("ichimoku_fx_mr");
  const intraday = by("intraday_flat");
  const fxNet = fx ? (fx.realisedLtd ?? 0) + (fx.openPnl ?? 0) : null;
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
      <Chip label="FX net" value={money(fxNet, fx?.currency ?? "GBP")}
        tone={fxNet != null && fxNet >= 0 ? "good" : fxNet != null ? "bad" : undefined} />
      <Chip label="intraday_flat" value={money(intraday?.realisedLtd, intraday?.currency ?? "GBP")}
        tone={(intraday?.realisedLtd ?? 0) < 0 ? "bad" : undefined} sub="realized" />
    </div>
  );
}
