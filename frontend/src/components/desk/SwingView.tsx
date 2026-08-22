/**
 * SwingView — the morning list.
 *
 * Owner: "what I need at least are some symbols where I can get in and get out
 * after making some money ... I can get into market in morning placing order at
 * certain price and booking the profit target in that order."
 *
 * So every row is a BRACKET ORDER: entry, target, stop. Nothing else.
 *
 * This is the only screen on the desk built from a strategy that cleared
 * pre-registered gates (MEAN_REVERSION_GATES_V1.md, committed 6c9f330 BEFORE
 * the run). The evidence is shown inline rather than buried, and so are the
 * limits — a screen that states 62% win also has to state that 38% lose.
 *
 * It rebuilds after the daily harvest (22:00, plus a 12:00 catch-up if the
 * nightly harvest failed and backfilled late) and makes NO IBKR calls, so it
 * can never compete for the market-data session the options desk needs.
 *
 * NOT every 20 minutes — the header and the on-screen badge both said that,
 * and both were false. The signal is computed on a SETTLED daily bar, so it
 * cannot change until the next close lands. Recomputing it intraday returns an
 * identical list while LOOKING live, which is worse than an honest timestamp.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)" };
type Resp = Awaited<ReturnType<typeof api.swingCandidates>>;

export function SwingView() {
  const [d, setD] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    api.swingCandidates().then((r) => { setD(r); setErr(null); })
      .catch((e) => setErr(String((e as Error)?.message || e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t); }, [load]);

  if (err) return <div style={{ padding: 16, color: TONE.bad }}>Swing list unavailable: {err}</div>;
  if (!d) return <div style={{ padding: 16, color: "var(--text-dim)" }}>Loading…</div>;

  const a = d.artifact;
  const ago = Math.round((Date.now() - new Date(a.as_of_utc).getTime()) / 60000);

  return (
    <div style={{ padding: "8px 4px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Swing candidates</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          signal bar {a.signal_bar} · rebuilt {ago}m ago · {a.count} candidate{a.count === 1 ? "" : "s"}
        </span>
      </div>

      <div style={{ fontSize: 12, color: "var(--text-dim)", margin: "6px 0 12px", lineHeight: 1.6 }}>
        <b>Entry</b> {a.rule.entry} · <b>Target</b> {a.rule.target} · <b>Stop</b> {a.rule.stop} ·
        exit by {a.rule.timeout}. Each row is placeable as one bracket order.
      </div>

      {/* Evidence inline — this is the only desk surface with graded evidence,
          and hiding it would waste the one thing that makes it trustworthy. */}
      <div style={{ border: `1px solid ${TONE.ok}55`, background: `${TONE.ok}0e`, borderRadius: 8,
                    padding: "8px 12px", marginBottom: 12, fontSize: 12, lineHeight: 1.6 }}>
        <b style={{ color: TONE.ok }}>Backtested</b> — {a.evidence.trades.toLocaleString()} trades ·{" "}
        <b>{a.evidence.win_rate_pct}% win</b> · {a.evidence.mean_per_trade_pct}%/trade ·{" "}
        median hold{" "}
        {a.evidence.median_hold_under_review ? (
          <b style={{ color: TONE.warn }} title="The study harness was never committed and the surviving log does not record hold length. An independent replay gives 8.">
            {a.evidence.median_hold_sessions}–{a.evidence.median_hold_replay_sessions} sessions (unverified)
          </b>
        ) : (<>{a.evidence.median_hold_sessions} sessions</>)}{" "}
        · worst{" "}
        {a.evidence.worst_trade_under_review ? (
          <b style={{ color: TONE.warn }}>
            {a.evidence.worst_trade_replay_pct}% (under reconciliation — assume the larger number)
          </b>
        ) : (<b style={{ color: TONE.bad }}>{a.evidence.worst_trade_pct}%</b>)}.
        <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 3 }}>
          Gates <code>{a.evidence.gates_file}</code> committed <code>{a.evidence.gates_commit}</code>{" "}
          BEFORE the run — see Research. {a.evidence.note}
        </div>
      </div>

      {a.count === 0 ? (
        <div style={{ padding: 16, border: "1px dashed var(--border)", borderRadius: 8,
                      color: "var(--text-dim)", fontSize: 13 }}>
          <b>No candidates right now.</b> The screen is deliberately selective — roughly 1–2 signals
          a day across the defined universe. An empty list is the screen working, not the screen broken:
          it fires only on a 2.5σ dip in a name still above its 200-day average.
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Tier", "Entry", "Target", "Stop", "Upside", "R:R", "Depth", "ATR%", "vs 200-SMA"].map((x) => (
                  <th key={x} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {a.candidates.map((c) => (
                <tr key={c.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{c.symbol}</td>
                  <td style={{ padding: "8px 10px" }}>
                    <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999,
                                   color: c.tier === "core" ? TONE.ok : TONE.warn,
                                   border: `1px solid ${(c.tier === "core" ? TONE.ok : TONE.warn)}55` }}>
                      {c.tier}
                    </span>
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>{c.entry_hint.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.ok }}>{c.target.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.bad }}>{c.stop.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.ok }}>+{c.target_pct.toFixed(1)}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>{c.reward_risk?.toFixed(2) ?? "—"}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{c.sigma_below.toFixed(1)}σ</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{c.atr_pct.toFixed(1)}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>+{c.pct_above_200sma.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* QUARANTINE — dropped symbols are stated, never silently omitted.
          Owner ruling 22 Aug: "if we get poisoned prices then we better drop
          that symbol, highlight the fact." A screen that is quietly short a
          name is a screen you cannot reason about. */}
      {a.quarantined && a.quarantined.length > 0 && (
        <div style={{ marginTop: 12, border: `1px solid ${TONE.warn}55`, background: `${TONE.warn}0e`,
                      borderRadius: 8, padding: "8px 12px", fontSize: 12, lineHeight: 1.6 }}>
          <b style={{ color: TONE.warn }}>
            {a.quarantined.length} symbol{a.quarantined.length === 1 ? "" : "s"} dropped — suspect price history
          </b>
          <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 3 }}>
            These carry a stored series that looks like a different instrument (wrong venue or wrong
            contract). It passes NaN/spike checks because the series is internally consistent — it is
            simply not this security. Mean reversion is the strategy most exposed to it: a wrong-venue
            series looks permanently, enormously cheap.
          </div>
          <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 11 }}>
            {a.quarantined.map((q) => (
              <div key={q.symbol}><b>{q.symbol}</b> — {q.detail}</div>
            ))}
          </div>
        </div>
      )}

      {/* Limits stated on the surface, not in a doc nobody opens. */}
      <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6 }}>
        <b>What this does not do:</b>
        <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
          {a.limits.map((x, i) => <li key={i}>{x}</li>)}
          <li>38% of these lose. The edge is the average across many, not any single row.</li>
        </ul>
      </div>
    </div>
  );
}
