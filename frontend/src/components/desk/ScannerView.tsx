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
import { replaySwing, LIVE_PARAMS, type Bar, type SwingParams, type SwingReplay } from "../../lib/tradeOdds";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
type Row = { symbol: string; tier: string; atr: number } & SwingReplay;

export function ScannerView() {
  const bars = useRef<Record<string, Bar[]>>({});
  const [rows, setRows] = useState<Row[] | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [p, setP] = useState<SwingParams>({ ...LIVE_PARAMS });
  const [minTrades, setMinTrades] = useState(8);
  const [onlyFiring, setOnlyFiring] = useState(false);
  const [sort, setSort] = useState<"mean" | "win" | "n" | "sigma">("mean");
  const [open, setOpen] = useState<string | null>(null);

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
      setProgress("computing…");
      const out: Row[] = [];
      for (const u of syms) {
        const b = bars.current[u.symbol];
        if (!b || b.length < 250) continue;
        const r = replaySwing(b, p);
        if (r) out.push({ symbol: u.symbol, tier: `${u.beta_tier ?? "?"}β/${u.volatility_tier ?? "?"}v`,
                          atr: u.atr_pct ?? 0, ...r });
      }
      setRows(out);
    } catch (e) { setErr(String((e as Error)?.message || e)); }
    finally { setProgress(null); }
  }, [p]);

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
          <div style={{ fontSize: 12, marginBottom: 8, lineHeight: 1.6 }}>
            <b style={{ color: firing.length ? TONE.ok : "var(--text-muted)" }}>
              {firing.length} firing today{firing.length ? `: ${firing.map((r) => r.symbol).join(", ")}` : ""}
            </b>
            {" · "}{view.length} of {rows.length} shown ({minTrades}+ trades)
            {" · "}{view.filter((r) => r.meanPct > 0).length} profitable
          </div>

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
