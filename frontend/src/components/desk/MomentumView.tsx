/**
 * MomentumView — the longer-hold sleeve.
 *
 * Owner: "If you want to trade MU's current strength, that's a different
 * strategy than this one" → "can we test that as well. with calculated risk we
 * shd be able to leverage" → "why not explore both".
 *
 * So this runs ALONGSIDE Swing, not instead of it. The two are deliberately
 * different animals and the screen says so out loud, because the failure mode
 * here is a user placing a momentum entry expecting a Swing-shaped exit:
 *
 *     Swing     4-day holds   62% win   +0.77%/trade
 *     Momentum  34-bar holds  47% win   +1.53%/trade
 *
 * The second strategy to clear pre-registered gates (MOMENTUM_GATES_V2.md,
 * committed ca494bf BEFORE the run — all six passed). Same data discipline as
 * Swing: bar cache only, zero IBKR calls, settled bars, poison quarantine.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
type Resp = Awaited<ReturnType<typeof api.momentumCandidates>>;

export function MomentumView() {
  const [d, setD] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    api.momentumCandidates().then((r) => { setD(r); setErr(null); })
      .catch((e) => setErr(String((e as Error)?.message || e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t); }, [load]);

  if (err) return <div style={{ padding: 16, color: TONE.bad }}>Momentum list unavailable: {err}</div>;
  if (!d) return <div style={{ padding: 16, color: "var(--text-dim)" }}>Loading…</div>;

  const a = d.artifact;
  const ago = Math.round((Date.now() - new Date(a.as_of_utc).getTime()) / 60000);

  return (
    <div style={{ padding: "8px 4px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Momentum candidates</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          signal bar {a.signal_bar} · {a.count} candidate{a.count === 1 ? "" : "s"} · refreshed {ago}m ago
        </span>
      </div>

      {/* THE distinction that matters. Placed above everything else because the
          one expensive mistake is treating a momentum row like a swing row. */}
      <div style={{ border: `1px solid ${TONE.warn}55`, background: `${TONE.warn}0e`, borderRadius: 8,
                    padding: "8px 12px", margin: "10px 0", fontSize: 12, lineHeight: 1.6 }}>
        <b style={{ color: TONE.warn }}>This is not the in-and-out trade.</b>{" "}
        Median hold is {a.evidence.median_hold_sessions} sessions — about seven weeks. It is a
        trailing-stop trade: you hold while it works and the stop follows the peak up. If you want
        entry-and-target-in-one-order, use <b>Swing</b>.
      </div>

      <div style={{ fontSize: 12, color: "var(--text-dim)", margin: "0 0 12px", lineHeight: 1.6 }}>
        <b>Entry</b> {a.rule.entry} · <b>Initial stop</b> {a.rule.stop} ·{" "}
        <b>Then trail</b> {a.rule.trailing} · exit by {a.rule.timeout}.
      </div>

      <div style={{ border: `1px solid ${TONE.ok}55`, background: `${TONE.ok}0e`, borderRadius: 8,
                    padding: "8px 12px", marginBottom: 12, fontSize: 12, lineHeight: 1.6 }}>
        <b style={{ color: TONE.ok }}>Backtested</b> — {a.evidence.trades.toLocaleString()} trades ·{" "}
        <b>{a.evidence.win_rate_pct}% win</b> · {a.evidence.mean_per_trade_pct}%/trade ·{" "}
        median hold {a.evidence.median_hold_sessions} sessions · worst {a.evidence.worst_trade_pct}%.
        <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 3 }}>
          Gates <code>{a.evidence.gates_file}</code> committed <code>{a.evidence.gates_commit}</code>{" "}
          BEFORE the run — see Research. {a.evidence.note}
        </div>
      </div>

      {a.count === 0 ? (
        <div style={{ padding: 16, border: "1px dashed var(--border)", borderRadius: 8,
                      color: "var(--text-dim)", fontSize: 13 }}>
          <b>No candidates on the last settled bar.</b> The entry needs a name in a confirmed uptrend
          that has just pulled back to its 10-day average — in a broad rally almost nothing has pulled
          back, and in a selloff almost nothing is still in an uptrend. An empty list is normal.
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Entry", "Initial stop", "Then trail", "vs 200-SMA", "vs 20-SMA", "ATR%", "off 52w high"].map((x) => (
                  <th key={x} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {a.candidates.map((c) => (
                <tr key={c.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{c.symbol}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>{c.entry_hint.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.bad }}>{c.stop.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>{c.trailing_pct.toFixed(0)}% off peak</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.ok }}>+{c.pct_above_200sma.toFixed(1)}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>+{c.pct_above_20sma.toFixed(1)}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{c.atr_pct?.toFixed(1) ?? "—"}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{c.off_52w_high_pct?.toFixed(1) ?? "—"}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {a.quarantined && a.quarantined.length > 0 && (
        <div style={{ marginTop: 12, border: `1px solid ${TONE.warn}55`, background: `${TONE.warn}0e`,
                      borderRadius: 8, padding: "8px 12px", fontSize: 12, lineHeight: 1.6 }}>
          <b style={{ color: TONE.warn }}>
            {a.quarantined.length} symbol{a.quarantined.length === 1 ? "" : "s"} dropped — suspect price history
          </b>
          <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 3 }}>
            Stored series that looks like a different instrument (wrong venue or wrong contract). It
            passes NaN and spike checks because it is internally consistent — it is simply not this
            security. Dropped rather than shown, per the 22 Aug ruling.
          </div>
          <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 11 }}>
            {a.quarantined.map((q) => (<div key={q.symbol}><b>{q.symbol}</b> — {q.detail}</div>))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6 }}>
        <b>What this does not do:</b>
        <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
          {a.limits.map((x, i) => <li key={i}>{x}</li>)}
        </ul>
      </div>
    </div>
  );
}
