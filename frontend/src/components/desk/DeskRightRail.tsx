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

  const { chartRows, brokers, breaks } = useMemo(() => buildSeries(points, tf), [points, tf]);

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
                {/* LEFT MARGIN IS 0, NOT NEGATIVE. It was -18 against a 42px
                    Y-axis, which pulled the labels out past the plot's left
                    edge and CLIPPED THEIR LEADING CHARACTER. Every negative
                    tick lost its minus sign, so "-5%" rendered as "5%" and the
                    axis read 5 / 3 / 0 / 3 / 5 — a drawdown displayed as a
                    gain, on the one chart whose whole job is to show whether
                    the account is up or down. This series DOES go negative
                    (NLV vs start was -2.54% the morning this was found). */}
                <LineChart data={chartRows} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#1b2233" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#6b7799" }} minTickGap={28} />
                  {/* An explicit + on gains, so a reader never has to infer
                      the sign from a character that might not have rendered. */}
                  <YAxis tick={{ fontSize: 9, fill: "#6b7799" }} width={46}
                         tickFormatter={(v) => `${v > 0 ? "+" : ""}${v}%`} />
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
            {breaks.length > 0 && (
              <div style={{ marginTop: 3, color: "#d29922" }}>
                {breaks.map((b) => (
                  <div key={`${b.broker}-${b.date}`}>
                    {b.broker} {b.date.slice(5)}: {b.pct > 0 ? "+" : ""}{b.pct.toFixed(0)}% in one
                    day — counted as a change in account SIZE, not a return.
                  </div>
                ))}
              </div>
            )}
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

  // ── A CAPITAL EVENT IS NOT A RETURN ─────────────────────────────────
  //
  // This used to be `(v - firstValue) / firstValue`, which silently treats
  // every change in the account's SIZE as performance. On IBKR_PAPER that
  // produced a +145% line:
  //
  //     2026-09-25    145,718
  //     2026-09-26    365,051   <- +150.5% in one day
  //     2026-09-29    250,628   <- -31.1%
  //     2026-10-21    247,206
  //
  // Nothing earned that. It is the 26-29 Sep phantom-shorts episode — short
  // proceeds inflating reported NLV, then a partial correction. The chart
  // asserted a gain that never happened, and the outlier flattened every
  // other series onto the zero line, so the one number that WAS real (the
  // desk's own "NLV vs start -2.54%", shown inches away) became invisible.
  // The panel contradicted itself and the wrong half was the loud half.
  //
  // So returns are CHAIN-LINKED day to day, and a daily move larger than
  // CAPITAL_EVENT_PCT is treated as a change in the account's size rather
  // than a result: the level resets, the cumulative return carries on
  // unbroken. That is the standard treatment for a deposit or withdrawal,
  // and a broker-side correction is the same shape.
  //
  // THE THRESHOLD IS A JUDGEMENT AND IS STATED AS ONE. A whole diversified
  // book does not move 25% in a session; a funding event, a reset or a
  // reconciliation does. It is deliberately far above any real daily move
  // and far below the 150% that triggered this, so it cannot quietly eat a
  // genuine bad day. Every break it finds is REPORTED under the chart with
  // its date and size — a silently smoothed chart would be a worse lie than
  // the spike it replaced.
  const CAPITAL_EVENT_PCT = 25;
  const breaks: Array<{ broker: string; date: string; pct: number }> = [];
  const lookup = new Map<string, Map<string, number>>(); // broker -> date -> cumulative %
  for (const b of brokers) {
    const series = byBroker.get(b)!.slice().sort((a, c) => a.date.localeCompare(c.date));
    const m = new Map<string, number>();
    let cum = 1;
    let prev: number | null = null;
    for (const p of series) {
      if (prev != null && prev !== 0) {
        const r = p.value / prev - 1;
        if (Math.abs(r) * 100 > CAPITAL_EVENT_PCT) {
          breaks.push({ broker: b, date: p.date, pct: r * 100 });
        } else {
          cum *= 1 + r;
        }
      }
      m.set(p.date, Number(((cum - 1) * 100).toFixed(2)));
      prev = p.value;
    }
    lookup.set(b, m);
  }

  const chartRows = dates.map((date) => {
    const row: Record<string, string | number> = { date: date.slice(5) }; // MM-DD
    for (const b of brokers) {
      const v = lookup.get(b)!.get(date);
      if (v != null) row[b] = v;
    }
    return row;
  });

  return { chartRows, brokers, breaks };
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
