/**
 * DeskRightRail — collapsible right rail for the /desk Portfolio view.
 *
 * Top: an account-value line chart with timeframe pills (1W · MTD · 1M · 3M ·
 * YTD · 1Y · All), reusing api.accountValueHistory (the same daily per-broker
 * snapshots EquityCurveCard uses). Each broker is a line, plotted as % change
 * since the first day in the window — raw values live on different
 * scales/currencies (T212 ~$50k vs IG ~£10M demo) and can't share a Y-axis,
 * so % change is the only honest shared axis (same rationale as
 * EquityCurveCard). The history only goes back to when capture shipped, so
 * longer windows simply show what exists — never padded/fabricated.
 *
 * Bottom: a "news coming soon" stub. There's no readily-available news feed
 * source on the frontend, so per the no-fabrication rule we render a stub
 * rather than invent items.
 *
 * Mobile: the page stacks this rail BELOW the work-area (see Desk.tsx grid),
 * and the rail can be collapsed to reclaim vertical space.
 */
import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../../api/client";

type Point = { broker: string; date: string; currency: string | null; value: number };

const TF = ["1W", "MTD", "1M", "3M", "YTD", "1Y", "All"] as const;
type Timeframe = (typeof TF)[number];

const BROKER_COLOR: Record<string, string> = {
  T212_DEMO: "#1fc16b", T212_LIVE: "#0ea5e9",
  IG_DEMO: "#4f8cff", IG_LIVE: "#4f8cff",
};
const colourFor = (b: string) => BROKER_COLOR[b] ?? "#8693b5";

export function DeskRightRail() {
  const [collapsed, setCollapsed] = useState(false);
  const [points, setPoints] = useState<Point[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tf, setTf] = useState<Timeframe>("1M");

  useEffect(() => {
    let live = true;
    const load = () => {
      // Pull a wide window once; the pills filter client-side.
      api.accountValueHistory(366)
        .then((r) => { if (live) { setPoints(r.points ?? []); setErr(r.error ?? null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (live) setLoading(false); });
    };
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const { chartRows, brokers } = useMemo(() => buildSeries(points, tf), [points, tf]);

  return (
    <aside
      style={{
        border: "1px solid #1b2233", borderRadius: 8,
        background: "rgba(255,255,255,0.015)", padding: 10,
        display: "flex", flexDirection: "column", gap: 10,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Account Value
        </div>
        <button
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand" : "Collapse"}
          style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)", fontSize: 14 }}
        >
          {collapsed ? "▸" : "▾"}
        </button>
      </div>

      {!collapsed && (
        <>
          {/* Timeframe pills (no dropdowns — state stays visible). */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {TF.map((t) => {
              const active = tf === t;
              return (
                <button
                  key={t}
                  onClick={() => setTf(t)}
                  style={{
                    background: active ? "var(--accent, #4f8cff)" : "transparent",
                    color: active ? "#0a0e17" : "var(--text-muted)",
                    border: `1px solid ${active ? "var(--accent, #4f8cff)" : "#1b2233"}`,
                    borderRadius: 4, padding: "2px 8px", fontSize: 10, fontWeight: 600, cursor: "pointer",
                  }}
                >
                  {t}
                </button>
              );
            })}
          </div>

          <div style={{ height: 180 }}>
            {loading && points.length === 0 ? (
              <Stub>Loading account value…</Stub>
            ) : err ? (
              <Stub tone="down">Chart unavailable: {err}</Stub>
            ) : chartRows.length < 2 ? (
              <Stub>Not enough history yet for this window.</Stub>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartRows} margin={{ top: 4, right: 6, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="#1b2233" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6b7799" }} minTickGap={28} />
                  <YAxis tick={{ fontSize: 9, fill: "#6b7799" }} tickFormatter={(v) => `${v}%`} width={42} />
                  <Tooltip
                    contentStyle={{ background: "#0d1117", border: "1px solid #1b2233", fontSize: 11 }}
                    formatter={(v: number, name: string) => [`${v.toFixed(2)}%`, name]}
                  />
                  {brokers.map((b) => (
                    <Line key={b} type="monotone" dataKey={b} stroke={colourFor(b)} dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
          <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
            % change since start of window · per broker (native scale), not blended.
          </div>
        </>
      )}

      {/* News / activity — no frontend source yet, honest stub. */}
      <div style={{ borderTop: "1px solid #1b2233", paddingTop: 8 }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6 }}>
          News & Activity
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          News feed coming soon.
        </div>
      </div>
    </aside>
  );
}

/** Build per-broker % -change-since-window-start rows for recharts. */
function buildSeries(points: Point[], tf: Timeframe) {
  const cutoff = windowStart(tf);
  const inWindow = points.filter((p) => {
    const d = new Date(p.date + "T00:00:00Z").getTime();
    return Number.isFinite(d) && d >= cutoff;
  });

  const byBroker = new Map<string, Point[]>();
  for (const p of inWindow) {
    if (!byBroker.has(p.broker)) byBroker.set(p.broker, []);
    byBroker.get(p.broker)!.push(p);
  }

  const brokers = [...byBroker.keys()].sort();
  const dates = [...new Set(inWindow.map((p) => p.date))].sort();

  // Per broker: % change vs its first in-window value.
  const base = new Map<string, number>();
  for (const b of brokers) {
    const series = byBroker.get(b)!.sort((a, c) => a.date.localeCompare(c.date));
    if (series.length) base.set(b, series[0].value);
  }
  const lookup = new Map<string, Map<string, number>>(); // broker -> date -> value
  for (const b of brokers) {
    const m = new Map<string, number>();
    for (const p of byBroker.get(b)!) m.set(p.date, p.value);
    lookup.set(b, m);
  }

  const chartRows = dates.map((date) => {
    const row: Record<string, string | number> = { date: date.slice(5) }; // MM-DD
    for (const b of brokers) {
      const v = lookup.get(b)!.get(date);
      const b0 = base.get(b);
      if (v != null && b0 != null && b0 !== 0) row[b] = Number((((v - b0) / b0) * 100).toFixed(2));
    }
    return row;
  });

  return { chartRows, brokers };
}

function windowStart(tf: Timeframe): number {
  const now = new Date();
  const d = new Date(now);
  switch (tf) {
    case "1W": d.setDate(d.getDate() - 7); break;
    case "MTD": d.setDate(1); break;
    case "1M": d.setMonth(d.getMonth() - 1); break;
    case "3M": d.setMonth(d.getMonth() - 3); break;
    case "YTD": return new Date(now.getFullYear(), 0, 1).getTime();
    case "1Y": d.setFullYear(d.getFullYear() - 1); break;
    case "All": return 0;
  }
  return d.getTime();
}

function Stub({ children, tone }: { children: React.ReactNode; tone?: "down" }) {
  return (
    <div
      style={{
        height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 11, color: tone === "down" ? "var(--down, #ef4444)" : "var(--text-muted)", textAlign: "center",
      }}
    >
      {children}
    </div>
  );
}
