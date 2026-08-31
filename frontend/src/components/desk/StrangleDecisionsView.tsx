import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../../api/client";

/**
 * Index-strangle decision history — what was decided each day, and WHY.
 *
 * Owner, 31 Aug 2026: "i need to be able to see these decisions for the daily
 * ones so i can ask another agent to verify how we doing with this strategy",
 * and "MCP as well as another screen".
 *
 * STAND-ASIDES ARE SHOWN, and they are the point. The edge of this strategy is
 * what the volatility gate REFUSES to trade. A screen of only the trades would
 * be a highlight reel and could not answer whether the gate is set correctly —
 * which is the question anyone reviewing this is actually being asked.
 *
 * PROVISIONAL rows are marked. Before a session opens there is no opening
 * price, so the strikes are priced off the previous close: a real decision, but
 * not a placeable trade. On 31 Aug that distinction mattered — NIFTY moved 110
 * points overnight and the pre-open strikes were badly lopsided by the time
 * they could have been placed.
 */
type Row = {
  market: string; as_of: string; decided_at_utc: string;
  decision: string; reason: string;
  vol_symbol: string | null; vol_index: number | null; vol_threshold: number | null;
  spot: number | null; spot_basis: string | null; provisional: boolean;
  session_state: string | null; expiry_kind: string | null; dte: number | null;
  put_strike: number | null; call_strike: number | null;
  outcome_pct: number | null; graded_at_utc: string | null;
};
type Summary = {
  market: string; evaluated: number; traded: number; declined: number;
  provisional: number; graded: number; mean_outcome_pct: number | null;
};

const TONE = { ok: "#0f8a5f", off: "#8b95a5", warn: "#d29922", bad: "#f85149" };

export function StrangleDecisionsView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [sum, setSum] = useState<Summary[]>([]);
  const [days, setDays] = useState(30);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        apiGet<{ rows: Row[] }>(`/api/strangle-decisions?days=${days}`),
        apiGet<{ rows: Summary[] }>(`/api/strangle-decisions/summary?days=${days}`),
      ]);
      setRows(d.rows || []); setSum(s.rows || []); setErr(null);
    } catch (e) { setErr(String((e as Error)?.message || e)); }
  }, [days]);

  useEffect(() => { void load(); }, [load]);

  if (err) return <div style={{ padding: 16, color: TONE.bad }}>Unavailable: {err}</div>;

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Strangle decisions</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          every evaluation, including the days we stood aside
        </span>
        <span style={{ marginLeft: "auto" }}>
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              style={{ marginLeft: 6, padding: "3px 9px", borderRadius: 5, fontSize: 12,
                       cursor: "pointer", color: "var(--text)",
                       background: d === days ? "var(--surface-2)" : "transparent",
                       border: "1px solid var(--border)" }}>{d}d</button>
          ))}
        </span>
      </div>

      {/* Traded and DECLINED side by side — the gate is the strategy, so a
          tally of only the trades cannot show whether it is set right. */}
      <table style={{ width: "100%", borderCollapse: "collapse", margin: "14px 0", fontSize: 13 }}>
        <thead><tr style={{ color: "var(--text-muted)", textAlign: "left" }}>
          <th style={{ padding: "6px 8px" }}>Market</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Evaluated</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Traded</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Declined</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Provisional</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Graded</th>
        </tr></thead>
        <tbody>
          {sum.map((s) => (
            <tr key={s.market} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "6px 8px", fontWeight: 600 }}>{s.market}</td>
              <td style={{ padding: "6px 8px", textAlign: "right" }}>{s.evaluated}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: TONE.ok }}>{s.traded}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: TONE.off }}>{s.declined}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: s.provisional ? TONE.warn : "inherit" }}>{s.provisional}</td>
              <td style={{ padding: "6px 8px", textAlign: "right" }}>{s.graded}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead><tr style={{ color: "var(--text-muted)", textAlign: "left" }}>
          <th style={{ padding: "6px 8px" }}>Date</th>
          <th style={{ padding: "6px 8px" }}>Market</th>
          <th style={{ padding: "6px 8px" }}>Decision</th>
          <th style={{ padding: "6px 8px" }}>Vol vs gate</th>
          <th style={{ padding: "6px 8px" }}>Strikes</th>
          <th style={{ padding: "6px 8px" }}>Why</th>
        </tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const traded = r.decision === "CANDIDATE";
            return (
              <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>{String(r.as_of).slice(0, 10)}</td>
                <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                  {r.market}
                  {r.expiry_kind && <span style={{ fontSize: 10, color: "var(--text-muted)" }}> {r.expiry_kind}</span>}
                </td>
                <td style={{ padding: "6px 8px", color: traded ? TONE.ok : TONE.off, whiteSpace: "nowrap" }}>
                  {traded ? "TRADED" : "stood aside"}
                  {r.provisional && <span style={{ color: TONE.warn, fontSize: 10 }}> · provisional</span>}
                </td>
                <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                  {r.vol_index ?? "—"} / {r.vol_threshold ?? "—"}
                </td>
                <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                  {r.put_strike ? `${r.put_strike.toLocaleString()} / ${r.call_strike?.toLocaleString()}` : "—"}
                </td>
                <td style={{ padding: "6px 8px", color: "var(--text-muted)" }}>{r.reason}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div style={{ marginTop: 14, fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
        <b style={{ color: "var(--text)" }}>Stood-aside rows are the point.</b> The edge of this
        strategy is what the volatility gate refuses; a list of only the trades could not tell you
        whether the gate is set correctly.
        <br />
        <b style={{ color: "var(--text)" }}>Provisional</b> means the strikes were priced off the
        previous close because the session had not opened — a real decision, but not a placeable
        trade.
        <br />
        <b style={{ color: "var(--text)" }}>Outcomes are ungraded</b> until after the session closes.
        Grading a decision before then would be the same lookahead this strategy has already had to
        be corrected for.
      </div>
    </div>
  );
}
