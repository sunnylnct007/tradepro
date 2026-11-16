/**
 * IntradayShadowCard — the intraday rules' forward record, NO ORDERS.
 *
 * Owner, 16 Nov 2026: "what abt the intraday one that u said u will test
 * everyday without placing". Rules 2 and 7 failed their two-year backtests;
 * each now builds a forward record, replayed on the harvested 5-minute bars
 * after every 5m harvest. A session counts only once it is over; trades on the
 * current session show as in progress. Missing or stale names are NAMED.
 *
 * Source: today-setups/intraday-shadow (tradepro-intraday-shadow).
 */
import { useEffect, useState } from "react";

import { api } from "../../api/client";

type Rec = { trades: number; win_pct?: number; avg_pct?: number; total_usd?: number; sessions?: number };
type Trade = {
  rule: string; date: string; symbol: string; side: string; entry_time: string; entry: number;
  exit: number; exit_why: string; pct: number; usd: number; day?: string | null; final?: boolean;
  breakout_volume_x?: number | null; breakout_volume?: string | null;
};
type Rule = { name: string; record: Rec; backtest: { avg_pct: number; note: string }; by_day: Record<string, Rec>;
  by_breakout_volume?: Record<string, Rec> | null; latest_session: Trade[] };
type Art = {
  as_of_utc: string; record_start: string; sessions: number; latest_session: string | null;
  latest_session_final: boolean | null; latest_day: string | null; universe_n: number;
  missing: string[]; stale: string[]; rules: Record<string, Rule>; placed_orders: number;
};

const UP = "#3fb950", DOWN = "#f85149", WARN = "#d29922";
const col = (v: number) => (v > 0 ? UP : v < 0 ? DOWN : "var(--text-dim)");
const sgn = (v: number, d = 2) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`;

function RecordLine({ r }: { r: Rec }) {
  if (!r.trades) return <span style={{ color: "var(--text-muted)" }}>no finished trades yet</span>;
  return (
    <span style={{ fontVariantNumeric: "tabular-nums" }}>
      {r.trades} trades over {r.sessions} session(s) · win {r.win_pct}% · avg{" "}
      <b style={{ color: col(r.avg_pct ?? 0) }}>{sgn(r.avg_pct ?? 0, 3)}%</b> a trade ·{" "}
      <b style={{ color: col(r.total_usd ?? 0) }}>{sgn(r.total_usd ?? 0, 0)} $</b> on $10k a trade
    </span>
  );
}

export function IntradayShadowCard() {
  const [a, setA] = useState<Art | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api.todaySetupsArtifact("intraday-shadow")
      .then((r) => { setA(r.artifact as Art); setErr(null); })
      .catch(() => setErr("not published yet — it publishes after the next 5-minute harvest"));
    void load();
    const t = setInterval(load, 120_000);
    return () => clearInterval(t);
  }, []);

  const frame = { border: "1px solid var(--border)", borderRadius: 8, padding: "12px 14px", marginBottom: 16 };
  if (!a) {
    return (
      <div style={frame}>
        <b>Intraday rules — forward record (no orders)</b>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 4 }}>{err ?? "Loading…"}</div>
      </div>
    );
  }
  const mins = Math.round((Date.now() - new Date(a.as_of_utc).getTime()) / 60000);
  return (
    <div style={frame}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
        <b style={{ fontSize: 15 }}>Intraday rules — forward record (no orders)</b>
        <span style={{ fontSize: 11.5, color: mins > 120 ? WARN : "var(--text-muted)" }}>
          updated {mins < 60 ? `${mins} min` : `${Math.round(mins / 60)} h`} ago
        </span>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4, lineHeight: 1.55 }}>
        Both rules FAILED their two-year backtests. Each is replayed every session on the harvested 5-minute bars
        of {a.universe_n} names, from {a.record_start}, to see whether live sessions agree. Nothing is ever sent to a
        broker ({a.placed_orders} orders). {a.sessions} finished session(s) so far.
        {(a.missing.length > 0 || a.stale.length > 0) && (
          <span style={{ color: WARN }}> Not covered: {[...a.missing, ...a.stale].join(", ")}.</span>
        )}
      </div>
      {Object.entries(a.rules).map(([key, r]) => (
        <div key={key} style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
          <div style={{ fontWeight: 700, fontSize: 13.5 }}>{r.name}</div>
          <div style={{ fontSize: 12.5, marginTop: 2 }}>
            Forward: <RecordLine r={r.record} />
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Backtest: {sgn(r.backtest.avg_pct)}% a trade — {r.backtest.note}
          </div>
          {r.record.trades > 0 && (
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              By day (QQQ): {(["Up", "Flat", "Down"] as const).map((d) => (
                <span key={d}> {d} {r.by_day[d]?.trades ? <>{r.by_day[d].trades} · {sgn(r.by_day[d].avg_pct ?? 0, 2)}%</> : "—"} ·</span>
              ))}
            </div>
          )}
          {r.by_breakout_volume && r.record.trades > 0 && (
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              Breakout volume (fixed line 1.5x the usual for that time of day):
              {(["high", "low"] as const).map((v) => (
                <span key={v}> {v} {r.by_breakout_volume?.[v]?.trades ? <>{r.by_breakout_volume[v].trades} · {sgn(r.by_breakout_volume[v].avg_pct ?? 0, 2)}%</> : "—"} ·</span>
              ))}
            </div>
          )}
          <div style={{ fontSize: 12, marginTop: 4 }}>
            <span style={{ color: "var(--text-dim)" }}>
              {a.latest_session} {a.latest_session_final ? `(finished${a.latest_day ? `, QQQ ${a.latest_day}` : ""})` : "(in progress)"}:
            </span>
            {r.latest_session.length === 0 ? <span style={{ color: "var(--text-muted)" }}> no signal</span> : (
              r.latest_session.map((t) => (
                <div key={`${t.symbol}-${t.entry_time}`} style={{ fontVariantNumeric: "tabular-nums", paddingLeft: 10 }}>
                  <b>{t.symbol}</b> {t.side} at {t.entry.toFixed(2)} ({t.entry_time} ET) → {t.exit.toFixed(2)} · {t.exit_why}
                  {t.breakout_volume_x != null && <span style={{ color: "var(--text-muted)" }}> · breakout volume {t.breakout_volume_x}x ({t.breakout_volume})</span>}
                  {t.final !== false && <> · <span style={{ color: col(t.pct) }}>{sgn(t.pct)}% ({sgn(t.usd, 0)} $)</span></>}
                </div>
              ))
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
