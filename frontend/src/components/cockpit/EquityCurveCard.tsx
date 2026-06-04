/**
 * EquityCurveCard — "is the account growing?" Step 2 of the P&L redesign.
 *
 * Plots each broker's account value over time as % CHANGE since the first
 * captured day — because the raw values live on different scales/currencies
 * (T212 ~$50k vs IG ~£10M demo) and can't share a Y-axis meaningfully. The
 * headline shows each broker's current value in its native currency plus the
 * absolute + % move, so "are we up or down, and by how much" is obvious.
 *
 * Data: /api/account-value/history (daily per-broker snapshots). The series
 * only goes back to when capture shipped — short at first, fills out daily.
 */
import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { CockpitCard } from "../CockpitCard";
import { api } from "../../api/client";

type Point = { broker: string; date: string; currency: string | null; value: number };

const BROKER_COLOR: Record<string, string> = {
  T212_DEMO: "#1fc16b",
  T212_LIVE: "#0ea5e9",
  IG_DEMO: "#4f8cff",
  IG_LIVE: "#4f8cff",
};
const ccySym = (c: string | null) => (c === "GBP" ? "£" : c === "USD" ? "$" : (c ? c + " " : ""));

export function EquityCurveCard({ onHide }: { onHide?: () => void }) {
  const [points, setPoints] = useState<Point[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    const load = () =>
      api.accountValueHistory(30)
        .then((r) => { if (live) { setPoints(r.points ?? []); setErr(r.error ?? null); } })
        .catch((e) => { if (live) setErr(e instanceof Error ? e.message : "failed"); })
        .finally(() => { if (live) setLoading(false); });
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const { rows, brokers, headline } = useMemo(() => {
    const byBroker = new Map<string, Point[]>();
    for (const p of points) {
      if (!byBroker.has(p.broker)) byBroker.set(p.broker, []);
      byBroker.get(p.broker)!.push(p);
    }
    const dates = [...new Set(points.map((p) => p.date))].sort();
    const brokers = [...byBroker.keys()].sort();
    // baseline (first captured value) per broker → % change series.
    const baseline = new Map<string, number>();
    for (const [b, ps] of byBroker) baseline.set(b, ps.slice().sort((a, c) => a.date.localeCompare(c.date))[0]?.value ?? 0);
    const valAt = new Map<string, Map<string, number>>();
    for (const [b, ps] of byBroker) valAt.set(b, new Map(ps.map((p) => [p.date, p.value])));
    const rows = dates.map((date) => {
      const row: Record<string, number | string | null> = { date };
      for (const b of brokers) {
        const v = valAt.get(b)!.get(date);
        const base = baseline.get(b) ?? 0;
        row[b] = v != null && base > 0 ? (v / base - 1) * 100 : null;
      }
      return row;
    });
    const headline = brokers.map((b) => {
      const ps = byBroker.get(b)!.slice().sort((a, c) => a.date.localeCompare(c.date));
      const first = ps[0], last = ps[ps.length - 1];
      const dAbs = last.value - first.value;
      const dPct = first.value > 0 ? (last.value / first.value - 1) * 100 : 0;
      return { broker: b, ccy: last.currency, value: last.value, dAbs, dPct, days: ps.length };
    });
    return { rows, brokers, headline };
  }, [points]);

  return (
    <CockpitCard id="equity-curve" title="Account value over time" fullWidth onHide={onHide}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
        broker balances (golden source) · indexed to % change since first capture · grows daily
      </div>

      {/* Headline: current value + move per broker, native currency. */}
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginBottom: 12 }}>
        {headline.map((h) => {
          const up = h.dAbs >= 0;
          return (
            <div key={h.broker}>
              <div style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                {h.broker}
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: "monospace" }}>
                {ccySym(h.ccy)}{h.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </div>
              <div style={{ fontSize: 12, fontFamily: "monospace", color: up ? "#1fc16b" : "#ef4444" }}>
                {up ? "+" : "−"}{ccySym(h.ccy)}{Math.abs(h.dAbs).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                {" "}({up ? "+" : ""}{h.dPct.toFixed(2)}%) · {h.days}d
              </div>
            </div>
          );
        })}
      </div>

      {loading && rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "16px 0" }}>Loading…</div>
      ) : err ? (
        <div style={{ fontSize: 12, color: "#ef4444", padding: "8px 0" }}>Account history unavailable: {err}</div>
      ) : rows.length < 2 ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "16px 0" }}>
          Building the curve — only {rows.length} day{rows.length === 1 ? "" : "s"} captured so far. The line fills out daily.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              tickFormatter={(d: string) => (d.length >= 10 ? d.slice(5) : d)} minTickGap={24} />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} width={48}
              tickFormatter={(v) => `${v > 0 ? "+" : ""}${v}%`} />
            <ReferenceLine y={0} stroke="var(--text-muted)" strokeDasharray="2 2" />
            <Tooltip
              contentStyle={{ background: "var(--bg)", border: "1px solid var(--border)", fontSize: 11 }}
              formatter={(v: number, name: string) => [`${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`, name]}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {brokers.map((b) => (
              <Line key={b} type="monotone" dataKey={b} name={b}
                stroke={BROKER_COLOR[b] ?? "#a855f7"} dot={false} strokeWidth={2}
                connectNulls isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </CockpitCard>
  );
}
