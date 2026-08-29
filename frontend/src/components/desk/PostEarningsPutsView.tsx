/**
 * PostEarningsPutsView — the post-earnings cash-secured put screen.
 *
 * Owner's strategy, in his words: "MRVL is a good stock and was trading at 240
 * before quarterly result and now it corrected to 220 so i can safely play a
 * put at 195. this strategy can work normally after every quarterly results".
 *
 * TWO LAYERS, AND THE SEPARATION IS THE POINT. The setup — report date, the
 * drop, SPY vs its 200-SMA, the strike, the size — comes from BARS ONLY. No
 * option data, so a dark chain cannot empty this board the way it emptied the
 * wheel screen on 28 Aug ("Pricing carried from the last priced screen", 30
 * rows). Premium and OI are a separate best-effort layer; when they are
 * missing the row still shows its strike.
 *
 * EVIDENCE, shown inline rather than buried, and so are the limits — a screen
 * that states 89.5% also has to state that this is ONE regime and that the
 * worst trade was -23.4%. Verdict on record: PAPER FORWARD TEST, NOT FUNDED.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { requestScreenRun, watchJob } from "../../firebase";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)" };

type Row = {
  symbol: string; report_date: string; sessions_since: number;
  report_move_pct: number | null; spot: number; strike: number;
  annual_vol_pct: number | null; size_factor: number; collateral_usd: number;
  why_not?: string;
};
type Market = { ok: boolean | null; reason: string; spy_close?: number;
                spy_sma200?: number; pct_above?: number; as_of?: string };

export function PostEarningsPutsView() {
  const [art, setArt] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runState, setRunState] = useState<string | null>(null);

  const load = useCallback(() => {
    api.postEarningsPuts()
      .then((r) => { setArt(r.artifact); setErr(null); })
      .catch((e) => setErr(String((e as Error)?.message || e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t); }, [load]);

  const runNow = useCallback(async () => {
    setRunState("queued…");
    try {
      const id = await requestScreenRun("post_earnings_puts");
      if (!id) { setRunState("unavailable — Firebase not configured"); return; }
      const off = await watchJob(id, (status, d) => {
        setRunState(status === "complete"
          ? `done — ${d.candidates ?? 0} candidate(s)`
          : status === "failed" ? `failed: ${String(d.error ?? "")}` : status);
        if (status === "complete" || status === "failed") { off(); load(); }
      });
    } catch (e) { setRunState(`failed: ${String((e as Error)?.message || e)}`); }
  }, [load]);

  if (err) return <div style={{ padding: 16, color: TONE.bad }}>Screen unavailable: {err}</div>;
  if (!art) return <div style={{ padding: 16, color: "var(--text-dim)" }}>Loading…</div>;

  const m: Market = art.market || { ok: null, reason: "unknown" };
  const cands: Row[] = art.candidates || [];
  const near: Row[] = art.near_misses || [];
  const ev = art.evidence || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Post-earnings puts</h2>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          as of {String(art.as_of_utc || "").slice(0, 16)}Z
        </span>
        <button onClick={runNow}
                style={{ marginLeft: "auto", padding: "6px 12px", borderRadius: 6,
                         border: "1px solid var(--border)", background: "var(--surface-2)",
                         color: "var(--text)", cursor: "pointer", fontSize: 13 }}>
          ▶ Run now
        </button>
        {runState && <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{runState}</span>}
      </div>

      {/* The market gate, stated as a fact with its numbers — not a badge. */}
      <div style={{ padding: 12, borderRadius: 8, fontSize: 14,
                    border: `1px solid ${m.ok ? TONE.ok : TONE.bad}33`,
                    background: m.ok ? "#1D9E7511" : "#D85A3011" }}>
        <b style={{ color: m.ok ? TONE.ok : TONE.bad }}>
          MARKET GATE {m.ok ? "OPEN" : "CLOSED"}
        </b>{" — "}{m.reason}
        {m.spy_close != null && (
          <span style={{ color: "var(--text-muted)" }}>
            {" "}· SPY {m.spy_close} vs 200-SMA {m.spy_sma200} ({(m.pct_above ?? 0) > 0 ? "+" : ""}
            {m.pct_above}%) as of {m.as_of}
          </span>
        )}
      </div>

      {cands.length === 0 ? (
        <div style={{ padding: 16, border: "1px dashed var(--border)", borderRadius: 8,
                      color: "var(--text-dim)", fontSize: 15 }}>
          <b>No candidates right now.</b>{" "}
          {art.evaluated != null && <>Checked <b>{art.evaluated}</b> recent reporters. </>}
          The setup needs a report-day drop of 8% or more, within 5 sessions, while SPY
          is above its 200-day average.
          {near.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                Recent reporters that did not qualify
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <tbody>
                  {near.slice(0, 8).map((r) => (
                    <tr key={r.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                      <td style={{ padding: "5px 8px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{r.symbol}</td>
                      <td style={{ padding: "5px 8px", color: "var(--text-muted)" }}>{r.report_date}</td>
                      <td style={{ padding: "5px 8px", fontFamily: "var(--font-mono)",
                                   color: (r.report_move_pct ?? 0) < 0 ? TONE.warn : TONE.dim }}>
                        {r.report_move_pct != null ? `${r.report_move_pct.toFixed(1)}%` : "—"}
                      </td>
                      <td style={{ padding: "5px 8px", color: "var(--text-muted)" }}>{r.why_not}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Reported", "Report move", "Spot", "Sell put at", "Vol", "Size", "Collateral"].map((x) => (
                  <th key={x} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cands.map((r) => (
                <tr key={r.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{r.symbol}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-muted)" }}>
                    {r.report_date}
                    <span style={{ fontSize: 12 }}> ({r.sessions_since}d ago)</span>
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.warn }}>
                    {r.report_move_pct?.toFixed(1)}%
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>{r.spot.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>
                    {r.strike.toFixed(2)}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>
                    {r.annual_vol_pct != null ? `${r.annual_vol_pct.toFixed(0)}%` : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>{r.size_factor.toFixed(2)}×</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>
                    ${r.collateral_usd.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ fontSize: 12.5, color: "var(--text-muted)", lineHeight: 1.6 }}>
        Strike and size come from <b>bars only</b> — no option data — so a dark chain
        cannot hide a setup. Size is scaled by each name's volatility, which is why a
        high-vol name shows a smaller collateral than its strike implies.
      </div>

      {/* Evidence AND limits together. A screen that quotes 89.5% has to quote
          the regime caveat in the same breath. */}
      <div style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 8, fontSize: 13 }}>
        <b>Evidence</b> — {ev.v2_trades ?? 229} trades, {ev.v2_win_pct ?? 89.5}% win,{" "}
        {ev.v2_mean_pct ?? 1.29}% mean per trade, worst {ev.v2_worst_pct ?? -23.4}%.
        Selling the same puts <i>without</i> the earnings trigger returned{" "}
        {ev.null_mean_pct ?? -0.15}% — the edge is in the trigger, not in being long.
        <div style={{ marginTop: 8, color: TONE.warn }}>
          <b>Limits.</b> Earnings history starts ~Oct 2020, so this is tested across{" "}
          <b>one market regime only</b> and says nothing about a sustained bear market.
          The "2022 was not a losing year" check passed on just <b>nine</b> events.
          Worst single trade after filtering and sizing: <b>−23.4%</b>.
        </div>
        <div style={{ marginTop: 8, fontWeight: 700, color: TONE.bad }}>
          {ev.verdict ?? "PAPER FORWARD TEST at small size — NOT FUNDED"}
        </div>
      </div>
    </div>
  );
}
