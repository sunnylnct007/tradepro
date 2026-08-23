/**
 * ScannerView — the stock scanner. Adjust the rule, scan the universe, drill in.
 *
 * Owner: "like principle of option scanner we can have our own stock scanner",
 * "all these things we should be able to adjust on screen and see probability",
 * and "tomorrow I should be able to evaluate from screen rather than asking
 * you." That last one is the actual requirement: everything I have been
 * printing into a chat window should be on a surface he can drive himself.
 *
 * HOW IT WORKS. Bars are fetched ONCE and cached in memory. Moving a slider
 * recomputes locally across the whole universe in milliseconds — no refetch,
 * because a scanner you have to wait ninety seconds for is a scanner nobody
 * adjusts.
 *
 * THE HONESTY PROBLEM THIS SCREEN CREATES, and how it is handled. A tool that
 * lets you move four numbers until the win rate looks good is a machine for
 * fooling yourself; it is precisely what pre-registered gates exist to
 * prevent. So the live settings are marked, any deviation is labelled
 * EXPLORATORY in the header, and the per-symbol trade count is always shown —
 * because the most seductive results here will be 90% win rates on three
 * trades.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { RuleChart } from "./RuleChart";
import { replaySwing, todayBarFrom5m, LIVE_PARAMS, type Bar, type SwingParams, type SwingReplay } from "../../lib/tradeOdds";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
type Row = { symbol: string; tier: string; atr: number } & SwingReplay;

export function ScannerView() {
  const bars = useRef<Record<string, Bar[]>>({});
  const intraday = useRef<Record<string, Bar[]>>({});
  const [preview, setPreview] = useState(false);
  const [previewDate, setPreviewDate] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [p, setP] = useState<SwingParams>({ ...LIVE_PARAMS });
  const [minTrades, setMinTrades] = useState(8);
  const [onlyFiring, setOnlyFiring] = useState(false);
  const [sort, setSort] = useState<"mean" | "win" | "n" | "sigma">("mean");
  const [open, setOpen] = useState<string | null>(null);
  const [sens, setSens] = useState<Array<{ label: string; v: number; n: number; win: number; mean: number; live: boolean }> | null>(null);

  const isLive = useMemo(() =>
    (Object.keys(LIVE_PARAMS) as (keyof SwingParams)[]).every((k) => p[k] === LIVE_PARAMS[k]),
    [p]);

  /** Fetch once, then recompute from the cache on every parameter change. */
  const scan = useCallback(async (refetch: boolean) => {
    setErr(null);
    try {
      const uni = (await api.tradeableUniverse()).artifact;
      const syms = uni.symbols;
      if (refetch || !Object.keys(bars.current).length) {
        for (let i = 0; i < syms.length; i++) {
          const s = syms[i].symbol;
          setProgress(`fetching ${i + 1}/${syms.length} · ${s}`);
          if (bars.current[s]) continue;
          try {
            const r = await api.ibkrBars({ symbol: s, resolution: "1d", limit: 6000 });
            bars.current[s] = (r.bars ?? []).filter((x) => x.high >= x.low && x.close > 0)
              .map((x) => ({ ts: x.ts, open: x.open, high: x.high, low: x.low, close: x.close }));
          } catch { bars.current[s] = []; }
        }
      }
      if (preview) {
        for (let i = 0; i < syms.length; i++) {
          const s2 = syms[i].symbol;
          if (intraday.current[s2]) continue;
          setProgress(`today's session ${i + 1}/${syms.length} · ${s2}`);
          try {
            const r = await api.ibkrBars({ symbol: s2, resolution: "5m", limit: 400 });
            intraday.current[s2] = (r.bars ?? []).filter((x) => x.high >= x.low && x.close > 0)
              .map((x) => ({ ts: x.ts, open: x.open, high: x.high, low: x.low, close: x.close }));
          } catch { intraday.current[s2] = []; }
        }
      }
      setProgress("computing…");
      const out: Row[] = [];
      let pd: string | null = null;
      for (const u of syms) {
        const b = bars.current[u.symbol];
        if (!b || b.length < 250) continue;
        let pv: Bar | null = null;
        if (preview && intraday.current[u.symbol]?.length) {
          pv = todayBarFrom5m(intraday.current[u.symbol], b[b.length - 1].ts.slice(0, 10));
          if (pv) pd = pv.ts;
        }
        const r = replaySwing(b, p, { previewBar: pv });
        if (r) out.push({ symbol: u.symbol, tier: `${u.beta_tier ?? "?"}β/${u.volatility_tier ?? "?"}v`,
                          atr: u.atr_pct ?? 0, ...r });
      }
      setRows(out); setPreviewDate(pd);
    } catch (e) { setErr(String((e as Error)?.message || e)); }
    finally { setProgress(null); }
  }, [p, preview]);

  /** THE LEGITIMATE USE OF THE SLIDERS.
   *
   * Not "which settings score best" — that is hunting, and it is how a
   * backtest lies. The question worth asking is "does this result depend on
   * the exact numbers we picked?" A rule that only works at 2.5σ and dies at
   * 2.4σ is a knife edge and should not be funded. One that holds across a
   * range is a real effect that happens to be tuned.
   *
   * So this sweeps each parameter AROUND the live value and reports the whole
   * neighbourhood, live setting marked. It answers fragility and gives no way
   * to read off a winner.
   */
  const runSensitivity = useCallback(() => {
    const syms = Object.keys(bars.current).filter((s) => (bars.current[s]?.length ?? 0) >= 250);
    if (!syms.length) return;
    const agg = (pp: SwingParams) => {
      let n = 0, wins = 0, sum = 0;
      for (const s of syms) {
        const r = replaySwing(bars.current[s], pp);
        if (!r || !r.n) continue;
        n += r.n; wins += Math.round((r.winPct / 100) * r.n); sum += r.meanPct * r.n;
      }
      return { n, win: n ? (100 * wins) / n : 0, mean: n ? sum / n : 0 };
    };
    const out: typeof sens = [];
    const sweeps: Array<[string, keyof SwingParams, number[]]> = [
      ["σ below mean", "sigma", [2.0, 2.25, 2.5, 2.75, 3.0]],
      ["stop %", "stopPct", [5, 6, 8, 10, 12]],
      ["hold sessions", "maxHold", [10, 15, 20, 30, 40]],
      ["mean window", "bbWindow", [10, 15, 20, 25, 30]],
    ];
    for (const [label, key, vals] of sweeps)
      for (const v of vals) {
        const a = agg({ ...LIVE_PARAMS, [key]: v });
        out.push({ label, v, ...a, live: LIVE_PARAMS[key] === v });
      }
    setSens(out);
  }, []);

  const view = useMemo(() => {
    if (!rows) return [];
    let v = rows.filter((r) => r.n >= minTrades);
    if (onlyFiring) v = v.filter((r) => r.firesNow);
    const key = { mean: (r: Row) => r.meanPct, win: (r: Row) => r.winPct,
                  n: (r: Row) => r.n, sigma: (r: Row) => r.sigmasBelow }[sort];
    return [...v].sort((a, b) => key(b) - key(a));
  }, [rows, minTrades, onlyFiring, sort]);

  const firing = rows?.filter((r) => r.firesNow) ?? [];
  const inp = { background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 6,
                padding: "5px 8px", color: "inherit", fontFamily: "var(--font-mono)", width: 62 } as const;
  const th = { padding: "6px 9px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap",
               textAlign: "left" as const };
  const td = { padding: "6px 9px", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" as const };

  return (
    <div style={{ padding: "8px 4px" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Scanner</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          the Swing rule with its numbers exposed — scan all 244, then open any row
        </span>
      </div>

      <div style={{ border: `1px solid ${isLive ? TONE.ok : TONE.warn}55`,
                    background: `${isLive ? TONE.ok : TONE.warn}0e`, borderRadius: 8,
                    padding: "8px 12px", margin: "10px 0", fontSize: 12, lineHeight: 1.6 }}>
        {isLive ? (
          <><b style={{ color: TONE.ok }}>LIVE SETTINGS</b> — exactly the rule trading on IBKR
          paper: 2.5σ below the 20-day mean while above the 200-day average, target the 20-day
          mean, −8% stop, 20 sessions. Backtested 2,310 trades · 72.8% win · +1.06%/trade.</>
        ) : (
          <><b style={{ color: TONE.warn }}>EXPLORATORY — not the live rule.</b> You have changed the
          settings, so these numbers carry no pre-registered evidence. Moving four numbers until a
          win rate looks good is how a backtest lies; three studies were rejected this month for
          less. Treat anything found here as a hypothesis needing its own gates.{" "}
          <button onClick={() => setP({ ...LIVE_PARAMS })}
                  style={{ ...inp, width: "auto", cursor: "pointer" }}>reset to live</button></>
        )}
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 10 }}>
        {([["σ below mean", "sigma", 0.1], ["stop %", "stopPct", 1],
           ["hold (sessions)", "maxHold", 1], ["mean window", "bbWindow", 1],
           ["trend window", "trendWindow", 10]] as const).map(([label, k, step]) => (
          <label key={k} style={{ fontSize: 11, color: "var(--text-dim)" }}>
            <div style={{ marginBottom: 3 }}>
              {label}{p[k] !== LIVE_PARAMS[k] && <span style={{ color: TONE.warn }}> ✎</span>}
            </div>
            <input type="number" step={step} value={p[k]} style={inp}
                   onChange={(e) => setP({ ...p, [k]: parseFloat(e.target.value) || 0 })} />
          </label>
        ))}
        <label style={{ fontSize: 11, color: "var(--text-dim)" }}>
          <div style={{ marginBottom: 3 }}>min trades</div>
          <input type="number" value={minTrades} style={inp}
                 onChange={(e) => setMinTrades(parseInt(e.target.value) || 0)} />
        </label>
        <button onClick={() => scan(false)} disabled={!!progress}
                style={{ ...inp, width: "auto", cursor: "pointer", fontWeight: 700 }}>
          {progress ? progress : rows ? "Recompute" : "Scan universe"}
        </button>
        <label style={{ fontSize: 11, color: "var(--text-dim)", display: "flex", gap: 5,
                        alignItems: "center", border: `1px solid ${preview ? TONE.warn : "var(--border)"}`,
                        borderRadius: 6, padding: "5px 8px" }}
               title="Build today's partial bar from the 5-minute lane and preview the rule on it">
          <input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />
          include today&apos;s session
        </label>
        {rows && (
          <button onClick={runSensitivity} style={{ ...inp, width: "auto", cursor: "pointer" }}
                  title="Sweep each number around the live value — does the edge depend on the exact settings?">
            How fragile is this?
          </button>
        )}
        {rows && (
          <label style={{ fontSize: 11, color: "var(--text-dim)", display: "flex", gap: 5, alignItems: "center" }}>
            <input type="checkbox" checked={onlyFiring} onChange={(e) => setOnlyFiring(e.target.checked)} />
            firing today only
          </label>
        )}
      </div>

      {err && <div style={{ color: TONE.bad, fontSize: 12, marginBottom: 10 }}>{err}</div>}
      {!rows && !progress && (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          First scan fetches 244 symbols and takes a minute. After that, changing any number
          recomputes instantly from memory — no refetch.
        </div>
      )}

      {rows && (
        <>
          {/* WHAT THIS IS COMPUTED ON. The harvest runs 21:30, so during a
              session the newest settled bar is yesterday's — a scan at 2pm is
              a scan of yesterday's close. Saying so is the difference between
              a stale number and a misleading one. */}
          {(() => {
            const st = rows[0]?.sessionsStale ?? 0;
            const tone = st <= 1 ? TONE.ok : st <= 3 ? TONE.warn : TONE.bad;
            return (
              <div style={{ border: `1px solid ${tone}55`, background: `${tone}0e`, borderRadius: 8,
                            padding: "7px 12px", marginBottom: 10, fontSize: 11, lineHeight: 1.7 }}>
                <b style={{ color: tone }}>
                  COMPUTED ON DAILY BARS TO {rows[0]?.lastBar}
                  {st === 0 ? " (current)" : ` · ${st} trading session${st === 1 ? "" : "s"} old`}
                </b>
                <div style={{ color: "var(--text-muted)" }}>
                  {preview && previewDate
                    ? <>Plus <b style={{ color: TONE.warn }}>today&apos;s partial bar for {previewDate}</b>,
                      assembled from the 5-minute lane — which harvests every 30 minutes, so it is at
                      most half an hour behind. No IBKR quote is used, so this cannot take the
                      market-data session.</>
                    : <>Daily closes only — <b>no live price, no intraday bar</b>. The harvest runs at
                      21:30, so during a session the newest settled bar is the previous close.</>}
                </div>
                {preview && (
                  <div style={{ color: TONE.warn, marginTop: 3 }}>
                    ⚠ PREVIEW, NOT A SIGNAL. This answers &ldquo;if the session closed here, would the
                    rule fire&rdquo;. The live strategy stays settled-bar-only because the backtest
                    measured settled closes — a name down 3% at 11am may close flat, and trading the
                    partial bar produces entries the evidence never covered.
                  </div>
                )}
              </div>
            );
          })()}
          <div style={{ fontSize: 12, marginBottom: 8, lineHeight: 1.6 }}>
            <b style={{ color: firing.length ? TONE.ok : "var(--text-muted)" }}>
              {firing.length} firing today{firing.length ? `: ${firing.map((r) => r.symbol).join(", ")}` : ""}
            </b>
            {" · "}{view.length} of {rows.length} shown ({minTrades}+ trades)
            {" · "}{view.filter((r) => r.meanPct > 0).length} profitable
          </div>

          {sens && (
            <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px",
                          marginBottom: 12, fontSize: 12 }}>
              <b>Is the edge a knife edge?</b>
              <div style={{ color: "var(--text-muted)", fontSize: 11, margin: "3px 0 8px", lineHeight: 1.6 }}>
                Each number swept around its live value, everything else held at live. This is the
                only honest reason to move these controls: a rule that works at 2.5σ and dies at
                2.4σ should not be funded. Read whether the row is FLAT, not which cell is highest.
              </div>
              <div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))" }}>
                {["σ below mean", "stop %", "hold sessions", "mean window"].map((grp) => {
                  const g = sens.filter((x) => x.label === grp);
                  const best = Math.max(...g.map((x) => x.mean));
                  const worst = Math.min(...g.map((x) => x.mean));
                  const flat = best - worst < 0.35;
                  return (
                    <div key={grp}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)" }}>
                        {grp} <span style={{ color: flat ? TONE.ok : TONE.warn }}>
                          — {flat ? "flat, robust" : `varies ${(best - worst).toFixed(2)}pp`}</span>
                      </div>
                      <table style={{ borderCollapse: "collapse", fontSize: 11, marginTop: 3, width: "100%" }}>
                        <tbody>
                          {g.map((x) => (
                            <tr key={x.v} style={{ background: x.live ? "rgba(29,158,117,0.10)" : undefined }}>
                              <td style={{ ...td, padding: "2px 6px", fontWeight: x.live ? 700 : 400 }}>
                                {x.v}{x.live ? " ← live" : ""}
                              </td>
                              <td style={{ ...td, padding: "2px 6px", color: "var(--text-dim)" }}>{x.n}</td>
                              <td style={{ ...td, padding: "2px 6px" }}>{x.win.toFixed(0)}%</td>
                              <td style={{ ...td, padding: "2px 6px", fontWeight: 700,
                                           color: x.mean > 0 ? TONE.ok : TONE.bad }}>
                                {x.mean > 0 ? "+" : ""}{x.mean.toFixed(2)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8, maxHeight: 560 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "var(--surface-2)", position: "sticky", top: 0 }}>
                  <th style={th}>Symbol</th><th style={th}>Today</th>
                  {([["trades", "n"], ["win%", "win"], ["mean%", "mean"]] as const).map(([l, k]) => (
                    <th key={k} style={{ ...th, cursor: "pointer",
                                         color: sort === k ? "var(--accent, #4f8cff)" : "var(--text-dim)" }}
                        onClick={() => setSort(k)}>{l} {sort === k ? "▼" : ""}</th>
                  ))}
                  <th style={th}>median%</th><th style={th}>worst%</th>
                  <th style={th}>hold</th><th style={th}>ATR%</th><th style={th}>Tier</th>
                </tr>
              </thead>
              <tbody>
                {view.map((r) => (
                  <>
                    <tr key={r.symbol} onClick={() => setOpen(open === r.symbol ? null : r.symbol)}
                        style={{ borderTop: "1px solid #141b2b", cursor: "pointer",
                                 background: r.firesNow ? `${TONE.ok}12`
                                   : open === r.symbol ? "rgba(255,255,255,0.03)" : undefined }}>
                      <td style={{ ...td, fontWeight: 700 }}>
                        <span style={{ color: "var(--text-dim)", fontSize: 9, marginRight: 5 }}>
                          {open === r.symbol ? "▼" : "▶"}</span>{r.symbol}
                      </td>
                      <td style={{ ...td, color: r.firesNow ? TONE.ok : "var(--text-muted)",
                                   fontWeight: r.firesNow ? 700 : 400 }}>
                        {r.firesNow ? "FIRES" : r.sigmasBelow < 0
                          ? `${Math.abs(r.sigmasBelow).toFixed(1)}σ above`
                          : `${r.sigmasBelow.toFixed(1)}σ below`}
                      </td>
                      <td style={{ ...td, color: r.n < 8 ? TONE.warn : "var(--text-dim)" }}>{r.n}</td>
                      <td style={td}>{r.winPct.toFixed(0)}%</td>
                      <td style={{ ...td, fontWeight: 700, color: r.meanPct > 0 ? TONE.ok : TONE.bad }}>
                        {r.meanPct > 0 ? "+" : ""}{r.meanPct.toFixed(2)}%
                      </td>
                      <td style={td}>{r.medianPct > 0 ? "+" : ""}{r.medianPct.toFixed(2)}%</td>
                      <td style={{ ...td, color: TONE.bad }}>{r.worstPct.toFixed(1)}%</td>
                      <td style={{ ...td, color: "var(--text-dim)" }}>{r.medianHold}</td>
                      <td style={{ ...td, color: "var(--text-dim)" }}>{r.atrPct.toFixed(1)}%</td>
                      <td style={{ ...td, color: "var(--text-dim)", fontSize: 10 }}>{r.tier}</td>
                    </tr>
                    {open === r.symbol && (
                      <tr key={r.symbol + "-d"}>
                        <td colSpan={10} style={{ padding: "10px 14px 14px", background: "rgba(255,255,255,0.02)" }}>
                          {bars.current[r.symbol] && (
                            <div style={{ marginBottom: 12 }}>
                              <RuleChart bars={bars.current[r.symbol]} p={p} trades={r.trades} />
                            </div>
                          )}
                          <div style={{ display: "grid", gap: 16,
                                        gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))" }}>
                            <div>
                              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)" }}>
                                WHERE {r.symbol} IS TODAY · last bar {r.lastBar}
                              </div>
                              <div style={{ fontSize: 12, lineHeight: 1.8, marginTop: 4,
                                            fontFamily: "var(--font-mono)" }}>
                                close <b>{r.entry.toFixed(2)}</b><br />
                                {r.sigmasBelow >= 0 ? "below" : "ABOVE"} the 20-day mean by{" "}
                                <b>{Math.abs(r.sigmasBelow).toFixed(2)}σ</b> (fires at {p.sigma}σ below)<br />
                                {r.vs200 > 0 ? "above" : "BELOW"} the {p.trendWindow}-day average by{" "}
                                <b style={{ color: r.vs200 > 0 ? TONE.ok : TONE.bad }}>
                                  {r.vs200.toFixed(1)}%</b>
                                {r.vs200 <= 0 && " — this alone blocks the rule"}
                              </div>
                              {r.firesNow && (
                                <div style={{ marginTop: 6, fontSize: 12, fontFamily: "var(--font-mono)",
                                              border: `1px solid ${TONE.ok}55`, borderRadius: 6, padding: "6px 9px" }}>
                                  entry <b>{r.entry.toFixed(2)}</b> · target{" "}
                                  <b style={{ color: TONE.ok }}>{r.target.toFixed(2)}</b>{" "}
                                  (+{r.targetPct.toFixed(1)}%) · stop{" "}
                                  <b style={{ color: TONE.bad }}>{r.stop.toFixed(2)}</b>
                                </div>
                              )}
                            </div>
                            <div>
                              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)" }}>
                                LAST {Math.min(8, r.trades.length)} TRADES ON {r.symbol}
                                {r.n < 8 && <span style={{ color: TONE.warn }}> · only {r.n} ever — too few to mean much</span>}
                              </div>
                              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, marginTop: 4 }}>
                                <thead><tr style={{ color: "var(--text-dim)" }}>
                                  {["signal", "entry", "exit", "why", "bars", "P&L"].map((x) => (
                                    <th key={x} style={{ padding: "2px 6px", textAlign: "left" }}>{x}</th>))}
                                </tr></thead>
                                <tbody>
                                  {r.trades.slice(-8).reverse().map((t, k) => (
                                    <tr key={k} style={{ borderTop: "1px solid #141b2b" }}>
                                      <td style={{ ...td, padding: "2px 6px" }}>{t.signal}</td>
                                      <td style={{ ...td, padding: "2px 6px" }}>{t.entry.toFixed(2)}</td>
                                      <td style={{ ...td, padding: "2px 6px" }}>{t.exitPx.toFixed(2)}</td>
                                      <td style={{ ...td, padding: "2px 6px", color: "var(--text-muted)" }}>{t.why}</td>
                                      <td style={{ ...td, padding: "2px 6px" }}>{t.bars}</td>
                                      <td style={{ ...td, padding: "2px 6px", fontWeight: 700,
                                                   color: t.pct > 0 ? TONE.ok : TONE.bad }}>
                                        {t.pct > 0 ? "+" : ""}{t.pct.toFixed(2)}%</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8, lineHeight: 1.6 }}>
            <b>Read the trade count first.</b> 65 of 240 symbols have three or fewer trades in their
            whole history, and sorted by mean they fill both ends of the table — a single lucky trade
            reads as +34%, a single stop-out as −8%. That is sample size, not skill.
            Stored daily bars only, up to the last settled session — not live prices.
          </div>
        </>
      )}
    </div>
  );
}
