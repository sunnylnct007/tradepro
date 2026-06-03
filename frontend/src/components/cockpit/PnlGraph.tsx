/**
 * PnlGraph — "P&L at a glance" the trader asked for. One line PER STRATEGY
 * so you can see which desk is making/losing money, with a toggle between
 * TODAY (intraday points) and ALL-TIME (daily). total P&L = realised +
 * unrealised, sourced from the per-session snapshots the Mac pushes.
 *
 * Data:
 *   daily    → /api/paper/pnl/series?scope=daily    (one pt/strategy/day)
 *   intraday → /api/paper/pnl/series?scope=intraday (today's pts/strategy;
 *              populates going forward as paper_pnl_points accumulates)
 */
import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";

type Series = { strategyId: string; points: Array<{ ts: string; total: number }> };

// Stable, distinct colours per desk; falls back to the palette by index.
const STRATEGY_COLOR: Record<string, string> = {
  ichimoku_fx_mr: "#4f8cff",
  ichimoku_equity: "#1fc16b",
  intraday_flat: "#f59e0b",
};
const PALETTE = ["#4f8cff", "#1fc16b", "#f59e0b", "#a855f7", "#ef4444", "#14b8a6"];

export function PnlGraph({ onHide }: { onHide?: () => void }) {
  const [scope, setScope] = useState<"intraday" | "daily">(() => {
    if (typeof window === "undefined") return "intraday";
    return localStorage.getItem("cockpit.pnl.scope") === "daily" ? "daily" : "intraday";
  });
  const [series, setSeries] = useState<Series[]>([]);
  const [brokerByStrat, setBrokerByStrat] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    try { localStorage.setItem("cockpit.pnl.scope", scope); } catch { /* noop */ }
  }, [scope]);

  // Which strategy runs on which broker (config-driven, from the
  // strategy_broker_map). Only brokers that actually REPORT P&L give a
  // trustworthy line; today that's T212 (unrealisedAbs per position). IG
  // positions carry no P&L yet, so IG-routed desks (FX, intraday) are shown
  // as "pending" rather than plotted as a misleading ~0 line.
  useEffect(() => {
    let live = true;
    api.strategyBrokerMap()
      .then((bm) => {
        if (!live) return;
        const m: Record<string, string> = {};
        for (const row of bm.mappings) m[row.strategy_id] = row.broker;
        setBrokerByStrat(m);
      })
      .catch(() => { /* leave empty → treat all as pending until known */ });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const r = await api.pnlSeries(scope);
        if (!live) return;
        // Drop cost-basis-0 artifact points: when a historical snapshot had
        // avg_entry=0, unrealised was computed as mark×qty = the whole
        // position NOTIONAL (~$27k), not P&L — which made the graph read
        // −$26k while the desk was +$645. Open MTM on a ~$50k demo desk can't
        // plausibly swing > ~$15k in a day, so beyond that it's the artifact.
        const SANE = 15_000;
        setSeries(r.series.map((s) => ({
          strategyId: s.strategyId,
          points: s.points
            .filter((p) => Math.abs(p.total) < SANE)
            .map((p) => ({ ts: p.ts, total: p.total })),
        })));
        setErr(null);
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : "failed to load P&L");
      } finally {
        if (live) setLoading(false);
      }
    };
    setLoading(true);
    void load();
    const t = setInterval(load, 30_000);
    return () => { live = false; clearInterval(t); };
  }, [scope]);

  // Only plot a strategy that is ACTUALLY BOOKED to a broker that reports P&L
  // into the daily series. Today that's T212 (it returns unrealisedAbs per
  // position). Excludes:
  //   • orb / any PAPER / unmapped strategy — not booked to a broker at all,
  //     so it has no real P&L (it was plotting a garbage line).
  //   • IG-routed desks (FX, intraday) — IG position P&L isn't flowing into
  //     the daily SERIES yet (the desk cards show it, the series doesn't),
  //     so they'd be a misleading line. Shown as "pending" instead.
  const pnlCapable = (sid: string) =>
    /^(T212|IBKR)_/.test((brokerByStrat[sid] ?? "").toUpperCase());

  // Real lines we can stand behind vs strategies whose P&L isn't wired yet.
  const realSeries = useMemo(() => series.filter((s) => pnlCapable(s.strategyId)), [series, brokerByStrat]);
  const pendingStrategies = useMemo(
    () => series.filter((s) => !pnlCapable(s.strategyId)).map((s) => s.strategyId),
    [series, brokerByStrat]);

  // Merge per-strategy series into recharts rows keyed by timestamp. Each
  // row carries every strategy's total at that ts (null where it has no
  // point — connectNulls bridges the gaps so sparse intraday lines join).
  const { rows, strategies } = useMemo(() => {
    const tsSet = new Set<string>();
    for (const s of realSeries) for (const p of s.points) tsSet.add(p.ts);
    const sortedTs = [...tsSet].sort();
    const byStrat: Record<string, Map<string, number>> = {};
    for (const s of realSeries) {
      byStrat[s.strategyId] = new Map(s.points.map((p) => [p.ts, p.total]));
    }
    const rows = sortedTs.map((ts) => {
      const row: Record<string, number | string | null> = { ts };
      for (const s of realSeries) row[s.strategyId] = byStrat[s.strategyId].get(ts) ?? null;
      return row;
    });
    return { rows, strategies: realSeries.map((s) => s.strategyId) };
  }, [realSeries]);

  const fmtX = (ts: string) =>
    scope === "intraday" ? (ts.length >= 16 ? ts.slice(11, 16) : ts) : (ts.length >= 10 ? ts.slice(5, 10) : ts);
  const colorFor = (sid: string, i: number) => STRATEGY_COLOR[sid] ?? PALETTE[i % PALETTE.length];

  const totalNow = useMemo(() => {
    // Latest total across P&L-capable desks only (sum of last points).
    return realSeries.reduce((acc, s) => acc + (s.points.at(-1)?.total ?? 0), 0);
  }, [realSeries]);

  return (
    <CockpitCard
      id="pnl-graph"
      title="Open P&L (mark-to-market) — per strategy"
      badge={realSeries.length ? `$${totalNow.toFixed(0)}` : undefined}
      fullWidth
      onHide={onHide}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
        <ScopePill v="intraday" cur={scope} set={setScope} label="Today (intraday)" />
        <ScopePill v="daily" cur={scope} set={setScope} label="All-time (daily)" />
        <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: "auto" }}>
          open positions only (mark-to-market) · excludes closed/realised trades
        </span>
      </div>
      {pendingStrategies.length > 0 && (
        <div style={{ fontSize: 11, color: "#f59e0b", marginBottom: 8 }}>
          ⚠ P&amp;L not shown for {pendingStrategies.join(", ")} — IG broker P&amp;L
          integration pending (positions exposed, P&amp;L not yet).
        </div>
      )}

      {loading && rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "24px 0" }}>Loading…</div>
      ) : err ? (
        <div style={{ fontSize: 12, color: "#ef4444", padding: "12px 0" }}>P&L unavailable: {err}</div>
      ) : rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "24px 0" }}>
          {scope === "intraday"
            ? "No intraday P&L points yet today — the curve fills as each strategy pushes (every 5–15 min)."
            : "No daily P&L history yet."}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="ts" tickFormatter={fmtX} tick={{ fontSize: 10, fill: "var(--text-muted)" }} minTickGap={24} />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} width={48}
              tickFormatter={(v) => `$${v}`} />
            <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="2 2" />
            <Tooltip
              contentStyle={{ background: "var(--bg)", border: "1px solid var(--border)", fontSize: 11 }}
              labelFormatter={(ts) => String(ts)}
              formatter={(v: number, name: string) => [`$${Number(v).toFixed(2)}`, name]}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {strategies.map((sid, i) => (
              <Line key={sid} type="monotone" dataKey={sid} name={sid}
                stroke={colorFor(sid, i)} dot={false} strokeWidth={2} connectNulls
                isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </CockpitCard>
  );
}

function ScopePill({ v, cur, set, label }: {
  v: "intraday" | "daily"; cur: string; set: (s: "intraday" | "daily") => void; label: string;
}) {
  const active = v === cur;
  return (
    <button
      onClick={() => set(v)}
      style={{
        padding: "3px 10px", fontSize: 11, borderRadius: 999,
        border: `1px solid ${active ? "#4f8cff" : "var(--border)"}`,
        background: active ? "rgba(79,140,255,0.10)" : "transparent",
        color: active ? "#4f8cff" : "var(--text-dim)",
        cursor: "pointer", fontFamily: "monospace", letterSpacing: "0.02em",
      }}
    >
      {label}
    </button>
  );
}
