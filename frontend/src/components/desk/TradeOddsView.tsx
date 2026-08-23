/**
 * TradeOddsView — "if I rest a limit at X and target Y, what are the chances?"
 *
 * Owner: "we should be able to place an order at 920 and close at 950 and let
 * it run ... calculate the probability", for any symbol — MU, SNDK, SK hynix,
 * NVDA.
 *
 * It is a BASE RATE, not a forecast. Every historical bar is treated as if the
 * same order were resting there, and the tool counts what actually happened.
 *
 * The framing this screen exists to correct: "place at 920, target 950" sounds
 * like one probability and is two. With MU at 966, a limit at 920 is first a
 * bet that price falls 4.8% to reach you. P(target | filled) alone reads ~80%;
 * P(both) is ~45%. Quoting the former would flatter the trade badly, so the
 * chain is shown whole.
 *
 * Both eras are always shown side by side. A sweep of MU's last two years says
 * "target +25%" — because MU rose 11x over exactly that window. Momentum v3
 * was rejected for mistaking a regime for an edge; the same error is available
 * here and costs more, because a person acts on this number directly.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { barrierScan, sweepTargets, excursion, type Bar } from "../../lib/tradeOdds";
import { DipSuitePanel } from "./DipSuitePanel";
import { SymbolVerdictPanel } from "./SymbolVerdictPanel";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const RECENT_SESSIONS = 504; // ~2 years

export function TradeOddsView() {
  const [symbol, setSymbol] = useState("MU");
  const [entry, setEntry] = useState("");
  const [target, setTarget] = useState("");
  const [stopPct, setStopPct] = useState("-8");
  const [fillWindow, setFillWindow] = useState("10");
  const [tradeWindow, setTradeWindow] = useState("20");
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadBars = useCallback(async (sym: string) => {
    setBusy(true); setErr(null); setBars(null);
    try {
      const r = await api.ibkrBars({ symbol: sym.toUpperCase(), resolution: "1d", limit: 6000 });
      const b = (r.bars ?? [])
        .filter((x) => x.high >= x.low && x.close > 0)
        .map((x) => ({ ts: x.ts, open: x.open, high: x.high, low: x.low, close: x.close }));
      if (!b.length) { setErr(`No stored daily bars for ${sym.toUpperCase()}.`); return; }
      setBars(b);
      if (!entry) setEntry((b[b.length - 1].close * 0.95).toFixed(2));
      if (!target) setTarget((b[b.length - 1].close * 0.98).toFixed(2));
    } catch (e) {
      setErr(String((e as Error)?.message || e));
    } finally { setBusy(false); }
  }, [entry, target]);

  useEffect(() => { loadBars("MU"); /* eslint-disable-next-line */ }, []);

  const last = bars?.length ? bars[bars.length - 1] : null;
  const e = parseFloat(entry), t = parseFloat(target), sp = parseFloat(stopPct) / 100;
  const fw = Math.max(1, parseInt(fillWindow) || 10), tw = Math.max(1, parseInt(tradeWindow) || 20);
  const valid = !!(bars && last && e > 0 && t > e && sp < 0);

  let all = null, recent = null, sweepAll = null, sweepRecent = null, exAll = null, exRecent = null;
  if (valid && bars && last) {
    const o = { limitPct: e / last.close - 1, stopPct: sp, fillWindow: fw, tradeWindow: tw };
    const start = Math.max(0, bars.length - RECENT_SESSIONS);
    all = barrierScan(bars, { ...o, targetPct: t / e - 1 });
    recent = barrierScan(bars, { ...o, targetPct: t / e - 1, startIdx: start });
    sweepAll = sweepTargets(bars, o);
    sweepRecent = sweepTargets(bars, { ...o, startIdx: start });
    exAll = excursion(bars, tw);
    exRecent = excursion(bars, tw, start);
  }

  const inp = { background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 6,
                padding: "6px 8px", color: "inherit", fontFamily: "var(--font-mono)", width: 100 } as const;
  const th = { padding: "6px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" } as const;
  const td = { padding: "6px 10px", fontFamily: "var(--font-mono)" } as const;

  return (
    <div style={{ padding: "8px 4px" }}>
      <h2 style={{ margin: 0, fontSize: 18 }}>Trade odds</h2>
      <div style={{ fontSize: 12, color: "var(--text-dim)", margin: "6px 0 12px", lineHeight: 1.6 }}>
        Rest a limit, set a target, and see how often that order actually worked on this symbol&apos;s
        own history. A <b>base rate, not a forecast</b> — every past bar is replayed as if the same
        order were sitting there.
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
        {[["Symbol", symbol, setSymbol, 90], ["Limit / entry", entry, setEntry, 100],
          ["Target", target, setTarget, 100], ["Stop %", stopPct, setStopPct, 70],
          ["Wait (days)", fillWindow, setFillWindow, 80], ["Hold (days)", tradeWindow, setTradeWindow, 80],
        ].map(([label, val, set, w]) => (
          <label key={label as string} style={{ fontSize: 11, color: "var(--text-dim)" }}>
            <div style={{ marginBottom: 3 }}>{label as string}</div>
            <input value={val as string} style={{ ...inp, width: w as number }}
                   onChange={(ev) => (set as (s: string) => void)(ev.target.value)}
                   onKeyDown={(ev) => { if (ev.key === "Enter" && label === "Symbol") loadBars(symbol); }} />
          </label>
        ))}
        <button onClick={() => loadBars(symbol)} disabled={busy}
                style={{ ...inp, width: "auto", cursor: "pointer", fontWeight: 700 }}>
          {busy ? "Loading…" : "Load"}
        </button>
      </div>

      {err && <div style={{ color: TONE.bad, fontSize: 12, marginBottom: 10 }}>{err}</div>}

      {last && (
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10 }}>
          {symbol.toUpperCase()} — last settled close <b style={{ fontFamily: "var(--font-mono)" }}>{last.close.toFixed(2)}</b>{" "}
          ({last.ts.slice(0, 10)}) · {bars!.length.toLocaleString()} stored sessions from {bars![0].ts.slice(0, 10)}
          {bars!.length < 500 && (
            <div style={{ color: TONE.warn, marginTop: 4 }}>
              ⚠ Only {bars!.length} sessions of history — too little for these odds to mean much.
              Treat them as indicative at best.
            </div>
          )}
        </div>
      )}

      {!valid && bars && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Enter a limit price, a target above it, and a negative stop %.
        </div>
      )}

      {valid && all && recent && last && (
        <>
          {/* The chain, stated whole. */}
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px",
                        marginBottom: 12, fontSize: 12, lineHeight: 1.7 }}>
            <div style={{ color: "var(--text-dim)", marginBottom: 6 }}>
              Rest a limit at <b>{e.toFixed(2)}</b> ({(100 * (e / last.close - 1)).toFixed(1)}% from
              here) · target <b>{t.toFixed(2)}</b> (+{(100 * (t / e - 1)).toFixed(1)}% from entry) ·
              stop <b>{(e * (1 + sp)).toFixed(2)}</b> ({stopPct}%) · wait {fw}d, hold {tw}d
            </div>
            <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
              <thead><tr><th style={th} /><th style={th}>P(filled)</th><th style={th}>P(target | filled)</th>
                <th style={th}>P(both)</th><th style={th}>expectancy / filled order</th></tr></thead>
              <tbody>
                {([["all history", all], ["last 2 years", recent]] as const).map(([lbl, s]) => (
                  <tr key={lbl} style={{ borderTop: "1px solid #141b2b" }}>
                    <td style={{ ...td, color: "var(--text-dim)" }}>{lbl}</td>
                    <td style={td}>{pct(s.pFill)}</td>
                    <td style={td}>{pct(s.pTargetGivenFill)}</td>
                    <td style={{ ...td, fontWeight: 700 }}>{pct(s.pBoth)}</td>
                    <td style={{ ...td, fontWeight: 700, color: (s.meanOutcomePct ?? 0) > 0 ? TONE.ok : TONE.bad }}>
                      {s.meanOutcomePct == null ? "—" : `${s.meanOutcomePct > 0 ? "+" : ""}${s.meanOutcomePct}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 6 }}>
              <b>P(both)</b> is the one that happens to you — the limit has to fill before anything
              else can. A session touching both barriers is counted as a <b>stop</b>, because daily
              bars cannot say which came first; every number here is biased down, not up.
            </div>
          </div>

          {/* Target sweep, both eras. */}
          <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "var(--surface-2)" }}>
                  <th style={th} colSpan={2} />
                  <th style={{ ...th, textAlign: "center" }} colSpan={2}>all history</th>
                  <th style={{ ...th, textAlign: "center" }} colSpan={2}>last 2 years</th>
                </tr>
                <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                  <th style={th}>Target</th><th style={th}>R:R</th>
                  <th style={th}>P(hit | fill)</th><th style={th}>expectancy</th>
                  <th style={th}>P(hit | fill)</th><th style={th}>expectancy</th>
                </tr>
              </thead>
              <tbody>
                {sweepAll!.map((ra, idx) => {
                  const rr = sweepRecent![idx];
                  const bestA = Math.max(...sweepAll!.map((x) => x.expectancyPct ?? -99));
                  const bestR = Math.max(...sweepRecent!.map((x) => x.expectancyPct ?? -99));
                  const inA = ra.expectancyPct === bestA, inR = rr.expectancyPct === bestR;
                  return (
                    <tr key={ra.targetPct} style={{ borderTop: "1px solid #141b2b" }}>
                      <td style={{ ...td, fontWeight: 700 }}>+{ra.targetPct}%</td>
                      <td style={{ ...td, color: "var(--text-dim)" }}>{ra.rewardRisk}</td>
                      <td style={td}>{pct(ra.pTargetGivenFill)}</td>
                      <td style={{ ...td, fontWeight: inA ? 700 : 400, color: inA ? TONE.ok : "inherit" }}>
                        {ra.expectancyPct}%{inA && inR ? "  ← best in both" : inA ? "  ← best all-history" : ""}
                      </td>
                      <td style={td}>{pct(rr.pTargetGivenFill)}</td>
                      <td style={{ ...td, fontWeight: inR ? 700 : 400, color: inR && !inA ? TONE.warn : inR ? TONE.ok : "inherit" }}>
                        {rr.expectancyPct}%{inR && !inA ? "  ← recent only" : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.6 }}>
            A target that is best <b>only</b> in the recent column is a bet on the regime continuing,
            not a property of the symbol. Read expectancy, not hit rate: a 90% hit rate on a +1%
            target against a −8% stop loses money.
          </div>

          {exAll && exRecent && (
            <div style={{ marginTop: 12, border: "1px solid var(--border)", borderRadius: 8,
                          padding: "8px 12px", fontSize: 12, lineHeight: 1.7 }}>
              <b style={{ color: "var(--text-dim)" }}>HOW FAR IT ACTUALLY TRAVELS</b> — best upward move
              within {tw} sessions
              <div style={{ fontFamily: "var(--font-mono)", marginTop: 3 }}>
                all history — median +{exAll.median}% · 75th +{exAll.p75}% · 90th +{exAll.p90}%<br />
                last 2 years — median +{exRecent.median}% · 75th +{exRecent.p75}% · 90th +{exRecent.p90}%
              </div>
              <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 5 }}>
                This is the measured answer to the question support and resistance is usually asked.
                We tested drawn S/R over 76,260 touch events and both edges came out negative against
                random placebo lines, so it is not used anywhere on this desk. How far a symbol
                travels before reversing is the same instinct, counted rather than drawn — if the
                median move is +4% and you are targeting +12%, the target is fighting the symbol&apos;s
                own behaviour.
              </div>
            </div>
          )}

          <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6 }}>
            <b>What this does not do:</b>
            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
              <li>These are <b>not live prices</b>. Stored daily bars up to the last settled session.</li>
              <li>It is a base rate over the past, not a prediction. It knows nothing about earnings,
                  news or what the company is doing now.</li>
              <li><b>Volume is not used.</b> Momentum v3 gated on entry volume and was rejected — the
                  apparent edge exists only in the second half of the record and inverts before 2020.</li>
              <li>Daily bars only. Intraday order of high and low is unknown, so ties go to the stop.</li>
            </ul>
          </div>
        </>
      )}

      <SymbolVerdictPanel />
      <DipSuitePanel />
    </div>
  );
}
