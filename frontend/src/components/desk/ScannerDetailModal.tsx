/**
 * ScannerDetailModal — the Scanner's row detail, popped out with tabs.
 *
 * Owner: "the UI can be improved. same popup and expand we have for data
 * chart, and can have another tab in that popup to see the chart data
 * history."
 *
 * The detail used to expand INLINE inside the table row — a chart, a position
 * summary, an order calculator, a scorecard and a trade list all sharing the
 * width of one row, on a page that was already scrolling. Same overlay
 * mechanics as SymbolDetailModal (fixed backdrop, Esc or backdrop-click to
 * close) so the two popups behave identically.
 *
 * Tabs are in the order you would ASK the questions:
 *   Chart     what the rule sees, and where the symbol sits against it today
 *   Bars      the numbers BEHIND those chart lines, session by session
 *   Trades    every trade this rule has taken here, dated
 *   Record    how those compare to the universe, discounted for sample size
 *   My order  what happens if you ignore the rule and place your own limit
 *
 * The Bars tab is the one the owner asked for by name. It is deliberately the
 * derived series, not raw OHLC alone: the chart draws close, the 20-day mean,
 * the 2.5σ band and the 200-day average, so the table behind it shows those
 * same four numbers plus how many σ below the mean each session closed. That
 * makes "why is it not firing" answerable by reading a row instead of
 * eyeballing a line — and it makes the chart checkable rather than trusted.
 */
import { useEffect, useMemo, useState } from "react";
import { RuleChart } from "./RuleChart";
import { barrierScan, sweepTargets,
         type Bar, type SwingParams, type SwingReplay, type SymbolScore } from "../../lib/tradeOdds";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
type Row = { symbol: string; tier: string; atr: number; score?: SymbolScore } & SwingReplay;
type Tab = "chart" | "bars" | "trades" | "record" | "order";

const mean_ = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const pstdev = (xs: number[]) => {
  const m = mean_(xs);
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length);
};

export function ScannerDetailModal({ row, bars, p, entry, target, setEntry, setTarget, onClose }: {
  row: Row; bars: Bar[] | undefined; p: SwingParams;
  entry: string; target: string;
  setEntry: (v: string) => void; setTarget: (v: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("chart");
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const td: React.CSSProperties = { padding: "3px 10px 3px 0", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" };
  const th: React.CSSProperties = { padding: "3px 10px 5px 0", textAlign: "left", color: "var(--text-dim)", fontWeight: 600 };
  const inp: React.CSSProperties = { background: "var(--surface-2, #111826)", border: "1px solid var(--border)",
                                     borderRadius: 6, padding: "5px 8px", color: "inherit",
                                     fontFamily: "var(--font-mono)", width: 90 };

  /** The chart's four lines, as numbers. Last 120 settled sessions, newest first. */
  const series = useMemo(() => {
    if (!bars) return [];
    const c = bars.map((b) => b.close);
    const out: Array<{ ts: string; close: number; m20: number; band: number; s200: number;
                       sig: number; fires: boolean }> = [];
    const from = Math.max(p.trendWindow - 1, c.length - 120);
    for (let i = from; i < c.length; i++) {
      const w = c.slice(i - p.bbWindow + 1, i + 1);
      const m = mean_(w), sd = pstdev(w);
      const s200 = mean_(c.slice(i - p.trendWindow + 1, i + 1));
      const sig = sd > 0 ? (m - c[i]) / sd : 0;
      out.push({ ts: bars[i].ts.slice(0, 10), close: c[i], m20: m, band: m - p.sigma * sd,
                 s200, sig, fires: sd > 0 && c[i] < m - p.sigma * sd && c[i] > s200 });
    }
    return out.reverse();
  }, [bars, p]);

  const e = parseFloat(entry), tg = parseFloat(target);
  const ok = !!bars && e > 0 && tg > e;
  const sc = ok ? barrierScan(bars!, { limitPct: e / row.entry - 1, targetPct: tg / e - 1,
                                       stopPct: -0.08, fillWindow: 10, tradeWindow: 20 }) : null;
  const sw = ok ? sweepTargets(bars!, { limitPct: e / row.entry - 1, stopPct: -0.08,
                                        fillWindow: 10, tradeWindow: 20 }) : null;

  const TABS: Array<[Tab, string]> = [
    ["chart", "Chart"], ["bars", `Bars (${series.length})`], ["trades", `Trades (${row.n})`],
    ["record", "Record"], ["order", "My order"],
  ];

  return (
    <div onClick={onClose}
         style={{ position: "fixed", inset: 0, background: "rgba(3,6,12,0.72)", zIndex: 1000,
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div onClick={(ev) => ev.stopPropagation()}
           style={{ background: "var(--surface, #0b0f17)", border: "1px solid var(--border)",
                    borderRadius: 10, width: "min(1080px, 96vw)", height: "min(760px, 90vh)",
                    display: "flex", flexDirection: "column", overflow: "hidden" }}>

        {/* Header — the one line you need before choosing a tab. */}
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, padding: "12px 16px",
                      borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
          <b style={{ fontSize: 19, fontFamily: "var(--font-mono)" }}>{row.symbol}</b>
          <span style={{ fontSize: 12, color: "var(--text-muted)", border: "1px solid var(--border)",
                         borderRadius: 4, padding: "1px 6px" }}>{row.tier}</span>
          <span style={{ fontSize: 14, fontWeight: row.firesNow ? 700 : 400,
                         color: row.firesNow ? TONE.ok : "var(--text-muted)" }}>
            {row.firesNow ? "FIRES TODAY"
              : row.sigmasBelow < 0
                ? `${Math.abs(row.sigmasBelow).toFixed(2)}σ ABOVE its 20-day mean — this rule buys dips`
                : `${row.sigmasBelow.toFixed(2)}σ below its mean · needs ${p.sigma}σ`}
          </span>
          <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: "auto" }}>
            last settled bar {row.lastBar}
          </span>
          <button onClick={onClose} aria-label="close"
                  style={{ background: "transparent", border: "none", color: "var(--text-dim)",
                           fontSize: 22, lineHeight: 1, cursor: "pointer" }}>×</button>
        </div>

        <div style={{ display: "flex", gap: 4, padding: "8px 16px 0" }}>
          {TABS.map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
                    style={{ background: tab === k ? "rgba(255,255,255,0.07)" : "transparent",
                             border: "1px solid var(--border)",
                             borderBottomColor: tab === k ? "transparent" : "var(--border)",
                             borderRadius: "6px 6px 0 0", padding: "6px 14px", cursor: "pointer",
                             color: tab === k ? "inherit" : "var(--text-dim)", fontSize: 13,
                             fontWeight: tab === k ? 700 : 400 }}>
              {label}
            </button>
          ))}
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "14px 16px",
                      borderTop: "1px solid var(--border)" }}>

          {tab === "chart" && (bars ? (
            <>
              <RuleChart bars={bars} p={p} trades={row.trades} height={300} sessions={180} />
              <div style={{ display: "grid", gap: 16, marginTop: 12,
                            gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))" }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>
                    WHERE {row.symbol} IS TODAY
                  </div>
                  <div style={{ fontSize: 14, lineHeight: 1.8, marginTop: 4, fontFamily: "var(--font-mono)" }}>
                    close <b>{row.entry.toFixed(2)}</b><br />
                    {row.sigmasBelow >= 0 ? "below" : "ABOVE"} the {p.bbWindow}-day mean by{" "}
                    <b>{Math.abs(row.sigmasBelow).toFixed(2)}σ</b> (fires at {p.sigma}σ below)<br />
                    {row.vs200 > 0 ? "above" : "BELOW"} the {p.trendWindow}-day average by{" "}
                    <b style={{ color: row.vs200 > 0 ? TONE.ok : TONE.bad }}>{row.vs200.toFixed(1)}%</b>
                    {row.vs200 <= 0 && " — this alone blocks the rule"}
                  </div>
                </div>
                {row.firesNow && (
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>THE PLAN</div>
                    <div style={{ marginTop: 4, fontSize: 14, fontFamily: "var(--font-mono)",
                                  border: `1px solid ${TONE.ok}55`, borderRadius: 6, padding: "7px 10px",
                                  lineHeight: 1.8 }}>
                      entry <b>{row.entry.toFixed(2)}</b> · target{" "}
                      <b style={{ color: TONE.ok }}>{row.target.toFixed(2)}</b> (+{row.targetPct.toFixed(1)}%)<br />
                      stop <b style={{ color: TONE.bad }}>{row.stop.toFixed(2)}</b> · exit by {p.maxHold} sessions
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : <div style={{ color: "var(--text-muted)" }}>No bars loaded for {row.symbol}.</div>)}

          {tab === "bars" && (
            <>
              <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 8, lineHeight: 1.6 }}>
                The numbers behind the chart lines — last {series.length} settled sessions, newest
                first. Stored daily bars only, nothing live. <b>σ</b> is how far below the {p.bbWindow}-day
                mean that close sat; the rule needs <b>{p.sigma}σ</b> AND a close above the{" "}
                {p.trendWindow}-day average.
              </div>
              <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
                <thead><tr>{["Date", "Close", `${p.bbWindow}d mean`, `${p.sigma}σ band`,
                             `${p.trendWindow}d avg`, "σ below", ""].map((h) =>
                  <th key={h} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {series.map((s) => (
                    <tr key={s.ts} style={{ borderTop: "1px solid #141b2b",
                                            background: s.fires ? `${TONE.ok}12` : undefined }}>
                      <td style={td}>{s.ts}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{s.close.toFixed(2)}</td>
                      <td style={td}>{s.m20.toFixed(2)}</td>
                      <td style={td}>{s.band.toFixed(2)}</td>
                      <td style={{ ...td, color: s.close > s.s200 ? "inherit" : TONE.bad }}>
                        {s.s200.toFixed(2)}</td>
                      <td style={{ ...td, color: s.sig >= p.sigma ? TONE.ok : "var(--text-dim)" }}>
                        {s.sig.toFixed(2)}</td>
                      <td style={{ ...td, color: TONE.ok, fontWeight: 700 }}>{s.fires ? "FIRES" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {tab === "trades" && (
            row.trades.length ? (
              <>
                <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 8, lineHeight: 1.6 }}>
                  Every time this rule has fired on {row.symbol}, newest first — the whole record,
                  not a sample of the good ones.
                  {row.n < 8 && <span style={{ color: TONE.warn }}> Only {row.n} ever: too few to mean much.</span>}
                </div>
                <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
                  <thead><tr>{["Signal", "Entry", "Exit", "On", "Why it closed", "Bars", "P&L"].map((h) =>
                    <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {[...row.trades].reverse().map((t, i) => (
                      <tr key={i} style={{ borderTop: "1px solid #141b2b" }}>
                        <td style={td}>{t.signal}</td>
                        <td style={td}>{t.entry.toFixed(2)}</td>
                        <td style={td}>{t.exitPx.toFixed(2)}</td>
                        <td style={td}>{t.exitDate}</td>
                        <td style={{ ...td, color: "var(--text-muted)" }}>{t.why}</td>
                        <td style={td}>{t.bars}</td>
                        <td style={{ ...td, fontWeight: 700, color: t.pct > 0 ? TONE.ok : TONE.bad }}>
                          {t.pct > 0 ? "+" : ""}{t.pct.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : <div style={{ color: "var(--text-muted)" }}>This rule has never fired on {row.symbol}.</div>
          )}

          {tab === "record" && (row.score && row.score.n > 0 ? (
            <div style={{ fontSize: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>
                IF {row.symbol} FIRES — what that ONE trade looks like
              </div>
              <div style={{ fontSize: 13, color: "var(--text-muted)", margin: "3px 0 8px", lineHeight: 1.6 }}>
                Not &ldquo;how will {row.symbol} do this quarter&rdquo; — it fires about{" "}
                {(60 * row.n / 4000).toFixed(2)} times in 12 weeks, so the honest question is what a
                single trade is worth when it does.
              </div>
              <table style={{ borderCollapse: "collapse", fontSize: 14 }}>
                <tbody>
                  <tr><td style={{ ...td, color: "var(--text-dim)" }}>{row.symbol}&apos;s own record</td>
                      <td style={{ ...td, fontWeight: 700, color: row.score.ownMean > 0 ? TONE.ok : TONE.bad }}>
                        {row.score.ownMean > 0 ? "+" : ""}{row.score.ownMean.toFixed(2)}%</td>
                      <td style={{ ...td, color: "var(--text-muted)" }}>
                        90% range {row.score.ownLo.toFixed(2)}% to {row.score.ownHi.toFixed(2)}% on{" "}
                        {row.score.n} trades</td></tr>
                  <tr><td style={{ ...td, color: "var(--text-dim)" }}>universe base rate</td>
                      <td style={td}>+{row.score.baseMean.toFixed(2)}%</td>
                      <td style={{ ...td, color: "var(--text-muted)" }}>
                        {row.score.baseWin.toFixed(0)}% win across every symbol</td></tr>
                  <tr style={{ borderTop: "1px solid #141b2b" }}>
                      <td style={{ ...td, color: "var(--text-dim)" }}><b>discounted for sample size</b></td>
                      <td style={{ ...td, fontWeight: 700,
                                   color: row.score.shrunkMean > row.score.baseMean ? TONE.ok : "inherit" }}>
                        {row.score.shrunkMean > 0 ? "+" : ""}{row.score.shrunkMean.toFixed(2)}%</td>
                      <td style={{ ...td, color: "var(--text-muted)" }}>
                        {(100 * row.score.weight).toFixed(0)}% its own record,{" "}
                        {(100 * (1 - row.score.weight)).toFixed(0)}% the base rate</td></tr>
                </tbody>
              </table>
              <div style={{ fontSize: 13, marginTop: 8, lineHeight: 1.6, maxWidth: 760,
                            color: row.score.verdict === "better" ? TONE.ok
                                 : row.score.verdict === "worse" ? TONE.bad : "var(--text-muted)" }}>
                {row.score.verdict === "too few trades"
                  ? `Only ${row.score.n} trades — too few to call. A bootstrap of a tiny sample gives a falsely narrow range: resample 3 wins and every draw is positive, so the symbol looks proven on no evidence. Its ${row.score.ownMean.toFixed(2)}% is shown, not trusted.`
                  : row.score.verdict === "better"
                  ? `Genuinely better than average — even the bottom of its range (${row.score.ownLo.toFixed(2)}%) clears the base rate.`
                  : row.score.verdict === "worse"
                  ? `Worse than average — even the TOP of its range (${row.score.ownHi.toFixed(2)}%) sits below the base rate.`
                  : `In line with the universe. Its ${row.score.ownMean.toFixed(2)}% looks better or worse, but ${row.score.n} trades cannot tell it apart from the +${row.score.baseMean.toFixed(2)}% average — the range spans it.`}
              </div>
            </div>
          ) : <div style={{ color: "var(--text-muted)" }}>No trades yet, so no record to score.</div>)}

          {tab === "order" && (
            <div style={{ fontSize: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)" }}>
                PLACE YOUR OWN ORDER ON {row.symbol}
              </div>
              <div style={{ fontSize: 13, color: "var(--text-muted)", margin: "3px 0 8px", lineHeight: 1.6 }}>
                Ignore the rule — pick any entry and target and see how often that order has worked
                here. Stop is held at −8%, the live rule&apos;s stop.
              </div>
              <div style={{ display: "flex", gap: 10, marginBottom: 10 }}>
                {([["entry", entry, setEntry], ["target", target, setTarget]] as const).map(([lbl, v, set_]) => (
                  <label key={lbl} style={{ fontSize: 12, color: "var(--text-dim)" }}>
                    <div>{lbl}</div>
                    <input value={v} placeholder={row.entry.toFixed(2)} style={inp}
                           onChange={(ev) => set_(ev.target.value)} />
                  </label>
                ))}
              </div>
              {!ok ? (
                <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
                  Enter a limit and a target above it.
                </div>
              ) : sc && (
                <>
                  <div style={{ fontFamily: "var(--font-mono)", lineHeight: 1.9 }}>
                    P(filled) <b>{Math.round(100 * (sc.pFill ?? 0))}%</b> · P(target | filled){" "}
                    <b>{Math.round(100 * (sc.pTargetGivenFill ?? 0))}%</b> · P(both){" "}
                    <b style={{ color: TONE.ok }}>{Math.round(100 * (sc.pBoth ?? 0))}%</b>
                  </div>
                  <div style={{ fontSize: 13, color: "var(--text-muted)", lineHeight: 1.6, maxWidth: 700 }}>
                    A limit below today&apos;s price is TWO bets — that it comes back to you at all,
                    then that it reaches your target. P(both) is what happens to you.
                  </div>
                  <table style={{ borderCollapse: "collapse", fontSize: 13, marginTop: 8 }}>
                    <thead><tr>{["Target", "Hit rate", "Expectancy"].map((h) =>
                      <th key={h} style={th}>{h}</th>)}</tr></thead>
                    <tbody>
                      {sw!.slice(0, 8).map((x) => (
                        <tr key={x.targetPct} style={{ borderTop: "1px solid #141b2b" }}>
                          <td style={td}>+{x.targetPct}%</td>
                          <td style={td}>{Math.round(100 * (x.pTargetGivenFill ?? 0))}%</td>
                          <td style={{ ...td, fontWeight: 700,
                                       color: (x.expectancyPct ?? 0) > 0 ? TONE.ok : TONE.bad }}>
                            {x.expectancyPct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 6 }}>
                    Read expectancy, not hit rate. A 90% hit rate on a +1% target against an 8% stop
                    loses money.
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
