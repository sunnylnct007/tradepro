/**
 * DailyEvaluationCard — one dated record per session for every strategy we run.
 *
 * Owner, 16 Nov 2026: "i just need every day evaluation" / "and then we can
 * evaluate what is the best strategy". Published after each US close by
 * tradepro-daily-evaluation and archived one row per day; this card lists the
 * days, newest first, and opens one to show the conditions it was tagged with
 * (fixed in EVALUATION_CONDITIONS_V1.md before any result) and each
 * strategy's result. Nothing here places an order.
 */
import { Fragment, useEffect, useState } from "react";

import { api } from "../../api/client";

const UP = "#3fb950", DOWN = "#f85149", WARN = "#d29922";
const col = (v: number) => (v > 0 ? UP : v < 0 ? DOWN : "var(--text-dim)");
const usd = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

type Entry = { date: string; artifact: any };

export function DailyEvaluationCard() {
  const [days, setDays] = useState<Entry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.todaySetupsHistory("daily-evaluation", 60)
      .then((r) => { setDays(r.entries); setOpen(r.entries[0]?.date ?? null); })
      .catch(() => setErr("no evaluation yet — the first is published after the next US close"));
  }, []);

  const frame = { border: "1px solid var(--border)", borderRadius: 8, padding: "12px 14px", marginBottom: 16 };
  if (!days || days.length === 0) {
    return (
      <div style={frame}>
        <b>Daily evaluation</b>
        <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginTop: 4 }}>{err ?? (days ? "No days recorded yet." : "Loading…")}</div>
      </div>
    );
  }
  return (
    <div style={frame}>
      <b style={{ fontSize: 15 }}>Daily evaluation</b>
      <span style={{ fontSize: 12, color: "var(--text-muted)" }}> · {days.length} session(s) recorded · built after each US close from settled closes · no orders</span>
      <div style={{ overflowX: "auto", marginTop: 8 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, minWidth: 760, fontVariantNumeric: "tabular-nums" }}>
          <thead>
            <tr style={{ color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.04em" }}>
              <th style={{ textAlign: "left", padding: "4px 6px" }}>Session</th>
              <th style={{ textAlign: "left", padding: "4px 6px" }}>Conditions</th>
              <th style={{ textAlign: "right", padding: "4px 6px" }}>Rotation day</th>
              <th style={{ textAlign: "right", padding: "4px 6px" }}>Rotation since start</th>
              <th style={{ textAlign: "right", padding: "4px 6px" }}>vs SPY / QQQ</th>
              <th style={{ textAlign: "right", padding: "4px 6px" }} title="The same rotation rebalanced monthly, every two weeks and weekly, from the decision the live book bought — paper models, no orders">Schedules: monthly / 2-wk / weekly</th>
              <th style={{ textAlign: "left", padding: "4px 6px" }}>Intraday (no orders)</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => {
              const a = d.artifact ?? {};
              const c = a.conditions ?? {};
              const b = a.strategies?.momentum_rotation?.book;
              const it = a.strategies?.intraday_rules?.trades_today ?? [];
              const itSum = it.reduce((x: number, t: any) => x + (t.final !== false ? t.usd : 0), 0);
              const sch = a.strategies?.rotation_schedules?.schedules;
              const key = String(d.date).slice(0, 10);
              return (
                <Fragment key={key}>
                  <tr onClick={() => setOpen(open === key ? null : key)} style={{ cursor: "pointer", borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "6px", fontWeight: 700 }}>{a.session ?? key}</td>
                    <td style={{ padding: "6px", color: "var(--text-dim)" }}>
                      {c.day_type ?? "?"} day · market {c.market_trend ?? "?"} · {c.volatility ?? "?"}
                      {c.sectors_in_selloff?.length ? <span style={{ color: WARN }}> · sell-off: {c.sectors_in_selloff.join(", ")}</span> : null}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right", color: b ? col(b.day_pnl_usd) : undefined }}>{b?.names ? usd(b.day_pnl_usd) : "—"}</td>
                    <td style={{ padding: "6px", textAlign: "right", color: b ? col(b.pnl_usd) : undefined }}>
                      {b?.names ? <>{usd(b.pnl_usd)} ({b.pnl_pct > 0 ? "+" : ""}{b.pnl_pct}%)</> : "—"}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right", color: "var(--text-dim)" }}>
                      {b?.benchmarks_since_pct ? Object.entries(b.benchmarks_since_pct).map(([k, v]) => `${k} ${(v as number) > 0 ? "+" : ""}${v}%`).join(" / ") : "—"}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right" }}>
                      {sch ? (["monthly", "biweekly", "weekly"] as const).map((k, n) => (
                        <span key={k}>{n > 0 && <span style={{ color: "var(--text-muted)" }}> / </span>}
                          <span style={{ color: sch[k] ? col(sch[k].since_start_pct) : undefined }}>
                            {sch[k] ? `${sch[k].since_start_pct > 0 ? "+" : ""}${sch[k].since_start_pct}%` : "—"}
                          </span>
                        </span>
                      )) : "—"}
                    </td>
                    <td style={{ padding: "6px", color: it.length ? col(itSum) : "var(--text-muted)" }}>
                      {it.length ? `${it.length} trade(s), ${usd(itSum)}` : "no signal"}
                    </td>
                  </tr>
                  {open === key && (
                    <tr>
                      <td colSpan={7} style={{ padding: "6px 6px 12px" }}>
                        <pre style={{ margin: 0, fontSize: 11.5, whiteSpace: "pre-wrap", color: "var(--text-dim)",
                          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{a.text ?? "(no text)"}</pre>
                        {a.errors && Object.keys(a.errors).length > 0 && (
                          <div style={{ color: WARN, fontSize: 12 }}>Not evaluated: {Object.entries(a.errors).map(([k, v]) => `${k}: ${v}`).join("; ")}</div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 6 }}>
        Conditions are fixed in EVALUATION_CONDITIONS_V1.md (written before any result). Schedules: the same rotation from the
        decision the live book bought (16 Nov open), rebalanced monthly (the live rule), every two weeks and weekly — whole
        sleeve incl. cash, 0.1% a trade, no orders. Monthly won the 2008–now study; this asks whether live markets agree.
        Click a day for every holding and trade.
      </div>
    </div>
  );
}
