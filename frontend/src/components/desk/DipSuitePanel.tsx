/**
 * DipSuitePanel — run the owner's dip-entry idea across a SUITE of symbols.
 *
 * Owner: "i should be able to run some of the symbols manually adhoc just to
 * see ... initial days i will not use them for real", and earlier, "we can
 * classify the high beta stocks etc, in fact we can run a suite of symbols in
 * one go."
 *
 * So: pick a tier (or type symbols), set the order shape once, and see how it
 * would have gone on every name at once.
 *
 * THE COLUMN THAT MATTERS IS EXPECTANCY, NOT WIN RATE, and the panel is built
 * to make that unavoidable. This exact strategy was backtested and REJECTED
 * (INTRADAY_DIP_GATES_V1.md): at a 0.5% target against an 8% stop it wins 66%
 * of the time and loses 0.41% per trade, because the ratio needs 94% to break
 * even. A screen showing only the win rate would be actively misleading, so
 * both are shown and a losing expectancy is coloured as a loss however good
 * the hit rate looks.
 *
 * Everything is graded pessimistically: if a session's low fills you AND its
 * high clears the target, daily bars cannot say which came first, so it is
 * assumed the high came first and you carry. The owner raised this himself.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { dipScan, benchmarkPerDay, type Bar } from "../../lib/tradeOdds";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
type Uni = Awaited<ReturnType<typeof api.tradeableUniverse>>["artifact"];
type Row = { symbol: string; tier: string } & NonNullable<ReturnType<typeof dipScan>> & { bench: number | null };

export function DipSuitePanel() {
  const [uni, setUni] = useState<Uni | null>(null);
  const [tier, setTier] = useState("high-beta");
  const [dip, setDip] = useState("1");
  const [tgt, setTgt] = useState("1");
  const [stop, setStop] = useState("-8");
  const [carry, setCarry] = useState("1");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [progress, setProgress] = useState<string | null>(null);

  useEffect(() => { api.tradeableUniverse().then((r) => setUni(r.artifact)).catch(() => setUni(null)); }, []);

  const picked = (uni?.symbols ?? []).filter((s) =>
    tier === "all" ? true
      : tier === "high-beta" ? s.beta_tier === "high"
      : tier === "low-beta" ? s.beta_tier === "low"
      : tier === "high-vol" ? s.volatility_tier === "high"
      : true);

  const run = useCallback(async () => {
    if (!uni) return;
    const d = parseFloat(dip), t = parseFloat(tgt), sp = parseFloat(stop), cy = parseInt(carry);
    if (!(d > 0 && t > 0 && sp < 0 && cy >= 1)) return;
    const out: Row[] = [];
    for (let i = 0; i < picked.length; i++) {
      const s = picked[i];
      setProgress(`${i + 1}/${picked.length} · ${s.symbol}`);
      try {
        const r = await api.ibkrBars({ symbol: s.symbol, resolution: "1d", limit: 6000 });
        const bars: Bar[] = (r.bars ?? []).filter((x) => x.high >= x.low && x.close > 0)
          .map((x) => ({ ts: x.ts, open: x.open, high: x.high, low: x.low, close: x.close }));
        if (bars.length < 250) continue;
        const sc = dipScan(bars, { dipPct: d, targetPct: t, stopPct: sp, carryDays: cy });
        if (sc) out.push({ symbol: s.symbol, tier: `${s.beta_tier ?? "?"}β/${s.volatility_tier ?? "?"}v`,
                           ...sc, bench: benchmarkPerDay(bars) });
      } catch { /* a symbol the store cannot serve is skipped, not fatal */ }
    }
    out.sort((a, b) => b.expPerDayHeld - a.expPerDayHeld);
    setRows(out); setProgress(null);
  }, [uni, picked, dip, tgt, stop, carry]);

  const inp = { background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 6,
                padding: "6px 8px", color: "inherit", fontFamily: "var(--font-mono)", width: 70 } as const;
  const th = { padding: "6px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" } as const;
  const td = { padding: "6px 10px", fontFamily: "var(--font-mono)" } as const;

  const winners = rows?.filter((r) => r.expPerDayHeld > (r.bench ?? 0)).length ?? 0;
  const profitable = rows?.filter((r) => r.expPerTrade > 0).length ?? 0;

  return (
    <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>Run a suite — dip from the open</h3>
      <div style={{ fontSize: 14, color: "var(--text-dim)", margin: "6px 0 10px", lineHeight: 1.6 }}>
        Rest a buy limit below each morning&apos;s open, take profit at the target, carry over if it
        does not fill the target, stop out below. Run across a whole tier at once.
      </div>

      <div style={{ border: `1px solid ${TONE.bad}55`, background: `${TONE.bad}0e`, borderRadius: 8,
                    padding: "8px 12px", marginBottom: 12, fontSize: 14, lineHeight: 1.6 }}>
        <b style={{ color: TONE.bad }}>This strategy was backtested and REJECTED.</b> At a 0.5% target
        against an 8% stop it wins <b>66%</b> of the time and loses <b>0.41% per trade</b> — a −8% stop
        against a +0.5% target needs a <b>94%</b> win rate to break even. The cells that did make money
        wanted a 5% target held 3–5 days, and even those beat being long only in the recent half of
        history and on half the symbols. Read the <b>expectancy</b> column, never the win rate.
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 10 }}>
        <label style={{ fontSize: 13, color: "var(--text-dim)" }}>
          <div style={{ marginBottom: 3 }}>Tier</div>
          <div style={{ display: "flex", gap: 4 }}>
            {["high-beta", "high-vol", "low-beta", "all"].map((t) => (
              <button key={t} onClick={() => setTier(t)}
                      style={{ ...inp, width: "auto", cursor: "pointer",
                               background: tier === t ? "var(--accent, #4f8cff)" : "var(--surface-2)" }}>{t}</button>
            ))}
          </div>
        </label>
        {([["Dip %", dip, setDip], ["Target %", tgt, setTgt], ["Stop %", stop, setStop],
           ["Carry days", carry, setCarry]] as const).map(([l, v, set]) => (
          <label key={l} style={{ fontSize: 13, color: "var(--text-dim)" }}>
            <div style={{ marginBottom: 3 }}>{l}</div>
            <input value={v} style={inp} onChange={(e) => set(e.target.value)} />
          </label>
        ))}
        <button onClick={run} disabled={!uni || !!progress}
                style={{ ...inp, width: "auto", cursor: "pointer", fontWeight: 700 }}>
          {progress ? progress : `Run ${picked.length} symbols`}
        </button>
      </div>

      {!uni && <div style={{ fontSize: 14, color: "var(--text-muted)" }}>Loading universe…</div>}

      {rows && (
        <>
          <div style={{ fontSize: 14, marginBottom: 8, lineHeight: 1.6 }}>
            {rows.length} symbols · <b style={{ color: profitable > rows.length / 2 ? TONE.ok : TONE.bad }}>
              {profitable} profitable per trade</b> · {winners} beat simply being long.
            {profitable < rows.length / 2 && (
              <span style={{ color: TONE.bad }}> Most of these lose money. That is the expected result.</span>
            )}
          </div>
          <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8, maxHeight: 460 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ background: "var(--surface-2)", textAlign: "left", position: "sticky", top: 0 }}>
                  {["Symbol", "Tier", "Fill %", "Trades", "Win %", "Exp / trade", "Exp / day held",
                    "vs long", "Hold"].map((x) => <th key={x} style={th}>{x}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const beats = r.bench != null && r.expPerDayHeld > r.bench;
                  return (
                    <tr key={r.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                      <td style={{ ...td, fontWeight: 700 }}>{r.symbol}</td>
                      <td style={{ ...td, color: "var(--text-dim)", fontSize: 13 }}>{r.tier}</td>
                      <td style={td}>{(100 * r.fillRate).toFixed(0)}%</td>
                      <td style={{ ...td, color: "var(--text-dim)" }}>{r.trades}</td>
                      {/* Win rate deliberately dimmed — it is the number that misleads here. */}
                      <td style={{ ...td, color: "var(--text-muted)" }}>{r.winRate.toFixed(0)}%</td>
                      <td style={{ ...td, fontWeight: 700, color: r.expPerTrade > 0 ? TONE.ok : TONE.bad }}>
                        {r.expPerTrade > 0 ? "+" : ""}{r.expPerTrade.toFixed(3)}%
                      </td>
                      <td style={{ ...td, color: r.expPerDayHeld > 0 ? TONE.ok : TONE.bad }}>
                        {r.expPerDayHeld > 0 ? "+" : ""}{r.expPerDayHeld.toFixed(4)}%
                      </td>
                      <td style={{ ...td, color: beats ? TONE.ok : TONE.warn }}>
                        {r.bench == null ? "—" : beats ? "beats" : "loses"}
                      </td>
                      <td style={{ ...td, color: "var(--text-dim)" }}>{r.meanHold.toFixed(1)}d</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
            Graded pessimistically: when a session&apos;s low fills you and its high clears the target,
            daily bars cannot say which came first, so it is assumed the high came first and the
            position carries. Same-day exits therefore cannot be measured here at all — that needs
            intraday bars, and the store holds a median of 14 sessions of them.
          </div>
        </>
      )}
    </div>
  );
}
