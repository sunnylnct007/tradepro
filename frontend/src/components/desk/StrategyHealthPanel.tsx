/**
 * StrategyHealthPanel — compact per-strategy traffic light on the cockpit.
 *
 * Collapsed by default to ONE line (a dot per strategy + "⚠ needs attention"
 * when any is blocked/stale) so it costs almost no screen space; click to expand
 * the per-strategy detail (status, reason, last-order timing, today's fills).
 *
 * Surfaces the gap a worker-level pill can't: one strategy can silently die
 * (skip/cancel every order) while the Mac daemon still pings "alive"
 * (feedback_surface_system_health_in_ui).
 */
import { useEffect, useState } from "react";
import { api } from "../../api/client";

const SEP = "#1b2233";

type Health = Awaited<ReturnType<typeof api.strategiesHealth>>["strategies"][number];

const STATUS: Record<Health["status"], { dot: string; label: string; color: string }> = {
  healthy: { dot: "🟢", label: "Trading",          color: "#3fb950" },
  idle:    { dot: "🟡", label: "Idle (no setup)",  color: "#d29922" },
  blocked: { dot: "🔴", label: "Blocked",          color: "#f85149" },
  stale:   { dot: "🔴", label: "No recent orders", color: "#f85149" },
  unknown: { dot: "⚪", label: "No orders yet",    color: "var(--text-muted)" },
};

function ago(mins: number | null): string {
  if (mins == null) return "never";
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h}h ${Math.round(mins % 60)}m ago`;
  return `${Math.round(h / 24)}d ago`;
}

type Perf = Awaited<ReturnType<typeof api.pnlByStrategy>>["rows"][number];

function money(n: number | null | undefined, ccy: string): string {
  if (n == null) return "—";
  const sym = ccy === "GBP" ? "£" : ccy === "USD" ? "$" : "";
  return `${n < 0 ? "-" : ""}${sym}${Math.abs(n).toFixed(0)}`;
}

export function StrategyHealthPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [rows, setRows] = useState<Health[] | null>(null);
  const [perf, setPerf] = useState<Record<string, Perf>>({});
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    let cancel = false;
    const load = async () => {
      try {
        const d = await api.strategiesHealth();
        if (!cancel) { setRows(d.strategies); setErr(null); }
      } catch (e) {
        if (!cancel) setErr(e instanceof Error ? e.message : "failed to load");
      }
      // Performance (win-rate / realised / trades) is optional context — never
      // let it break the health read.
      try {
        const p = await api.pnlByStrategy();
        if (!cancel) setPerf(Object.fromEntries(p.rows.map((r) => [r.strategyId, r])));
      } catch { /* perf optional */ }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => { cancel = true; clearInterval(t); };
  }, []);

  const anyBad = rows?.some((r) => r.status === "blocked" || r.status === "stale");
  const anyWarn = rows?.some((r) => r.status === "idle" || r.status === "unknown");

  return (
    <div style={{
      border: `1px solid ${anyBad ? "#f85149" : "var(--border)"}`,
      borderRadius: 8, background: "rgba(255,255,255,0.02)", overflow: "hidden",
    }}>
      {/* Collapsed traffic-light strip — minimal footprint, click to expand */}
      <div
        onClick={() => setOpen((o) => !o)}
        title="Strategy health — click for detail"
        style={{
          display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
          padding: "3px 8px", fontSize: 10.5, userSelect: "none",
          color: "var(--text-muted)",
        }}
      >
        <span style={{ fontWeight: 600 }}>Strategies</span>
        {/* one dot per strategy — the traffic light */}
        <span style={{ display: "flex", gap: 4, fontSize: 9 }}>
          {rows?.map((r) => (
            <span key={r.strategy} title={`${r.label}: ${STATUS[r.status].label} — ${r.reason}`}>
              {STATUS[r.status].dot}
            </span>
          ))}
          {!rows && !err && <span>…</span>}
          {err && <span style={{ color: "#f85149" }}>⚠</span>}
        </span>
        {anyBad
          ? <span style={{ color: "#f85149", fontWeight: 700 }}>⚠ needs attention</span>
          : anyWarn
            ? <span style={{ color: "#d29922" }}>some idle</span>
            : null}
        <span style={{ marginLeft: "auto", fontSize: 8 }}>{open ? "▲" : "▼"}</span>
      </div>

      {/* Expanded detail */}
      {open && rows && (
        <div style={{ padding: "2px 10px 8px" }}>
          {rows.map((r) => {
            const s = STATUS[r.status];
            const p = perf[r.strategy];
            const avg = p && p.trades && p.realisedLtd != null ? p.realisedLtd / p.trades : null;
            return (
              <div key={r.strategy} style={{ padding: "5px 4px", borderTop: `1px solid ${SEP}` }}>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(150px,1.3fr) 120px 1fr auto",
                  alignItems: "center", gap: 8, fontSize: 11.5,
                }}>
                  <span style={{ fontWeight: 600 }}>{r.label}</span>
                  <span style={{ color: s.color, fontWeight: 700 }}>{s.dot} {s.label}</span>
                  <span style={{ color: "var(--text-dim)" }}>{r.reason}</span>
                  <span style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                    last order {ago(r.minutesSinceOrder)} · {r.today.fills}F/{r.today.cancels}C
                    {r.today.pending ? `/${r.today.pending}P` : ""}
                  </span>
                </div>
                {/* Track record — so "selective and right" is visible vs "selective and losing". */}
                {p && (
                  <div style={{ display: "flex", gap: 12, fontSize: 10.5, marginTop: 3, paddingLeft: 2, flexWrap: "wrap" }}>
                    <PerfStat label="win" v={p.winRatePct != null ? `${p.winRatePct.toFixed(0)}%` : "—"} good={p.winRatePct != null ? p.winRatePct >= 50 : undefined} />
                    <PerfStat label="trades" v={`${p.trades}`} />
                    <PerfStat label="realised" v={money(p.realisedLtd, p.currency)} good={p.realisedLtd != null ? p.realisedLtd >= 0 : undefined} />
                    <PerfStat label="open" v={money(p.openPnl, p.currency)} good={p.openPnl != null ? p.openPnl >= 0 : undefined} />
                    <PerfStat label="avg/trade" v={money(avg, p.currency)} good={avg != null ? avg >= 0 : undefined} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** One compact "label value" perf stat; green when good, red when bad. */
function PerfStat({ label, v, good }: { label: string; v: string; good?: boolean }) {
  const color = good === undefined ? "var(--text-dim)" : good ? "#3fb950" : "#f85149";
  return (
    <span>
      <span style={{ opacity: 0.6 }}>{label} </span>
      <span style={{ color, fontWeight: 600 }}>{v}</span>
    </span>
  );
}
