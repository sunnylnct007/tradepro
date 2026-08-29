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
        {/* REFRESH. Owner: "why no rerun button on swing". There was nothing
            to press because the scan ran twice a day — so during a session the
            screen showed a scan from hours earlier. The job now runs every 30
            minutes (matching the 5m harvest that feeds the "Now" price), and
            this re-fetches the published result. It does NOT re-run the scan:
            that happens on the Mac, and the SIGNAL cannot change intraday
            anyway because it is computed on a settled bar. What DOES change is
            the latest price. */}
        <button onClick={load}
                title="Re-fetch the published scan. The scan itself re-runs every 30 minutes; the signal is fixed on the settled close, but the Now price moves."
                style={{ background: "var(--surface-2)", border: "1px solid var(--border)",
                         borderRadius: 6, padding: "3px 9px", color: "inherit",
                         cursor: "pointer", fontSize: 13 }}>
          ↻ Refresh
        </button>
        <span style={{ fontSize: 14, color: "var(--text-muted)" }}>
          signal bar {a.signal_bar} · rebuilt {ago}m ago · {a.count} candidate{a.count === 1 ? "" : "s"}
        </span>
      </div>

      <div style={{ fontSize: 14, color: "var(--text-dim)", margin: "6px 0 12px", lineHeight: 1.6 }}>
        <b>Entry</b> {a.rule.entry} · <b>Target</b> {a.rule.target} · <b>Stop</b> {a.rule.stop} ·
        exit by {a.rule.timeout}. Each row is placeable as one bracket order.
      </div>

      {/* Evidence inline — this is the only desk surface with graded evidence,
          and hiding it would waste the one thing that makes it trustworthy. */}
      <div style={{ border: `1px solid ${TONE.ok}55`, background: `${TONE.ok}0e`, borderRadius: 8,
                    padding: "8px 12px", marginBottom: 12, fontSize: 14, lineHeight: 1.6 }}>
        <b style={{ color: TONE.ok }}>Backtested</b> — {a.evidence.trades.toLocaleString()} trades ·{" "}
        <b>{a.evidence.win_rate_pct}% win</b> · {a.evidence.mean_per_trade_pct}%/trade ·{" "}
        median {a.evidence.median_per_trade_pct !== undefined && (
          <b style={{ color: TONE.ok }}>{a.evidence.median_per_trade_pct}%</b>
        )} · median hold {a.evidence.median_hold_sessions} sessions · worst{" "}
        <b style={{ color: TONE.bad }}>{a.evidence.worst_trade_pct}%</b>.
        <div style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 3 }}>
          Gates <code>{a.evidence.gates_file}</code> committed <code>{a.evidence.gates_commit}</code>{" "}
          BEFORE the run — see Research. {a.evidence.note}
          {a.evidence.harness && (
            <div style={{ marginTop: 3 }}>
              Harness <code>{a.evidence.harness}</code> is committed — this result can be re-run,
              which the previous one could not.
            </div>
          )}
        </div>
      </div>

      {/* The failure mode, stated where it cannot be missed. A screen that
          shows 66% win without showing WHEN that 66% does not apply is
          telling half a truth. */}
      {a.regime_dependence && (
        <div style={{ border: `1px solid ${TONE.bad}55`, background: `${TONE.bad}0e`, borderRadius: 8,
                      padding: "8px 12px", marginBottom: 12, fontSize: 14, lineHeight: 1.6 }}>
          <b style={{ color: TONE.bad }}>This loses money in a bear market.</b> Split by where the
          S&amp;P was when each trade opened:
          <table style={{ borderCollapse: "collapse", fontSize: 13, marginTop: 5 }}>
            <tbody>
              {([["S&P above its 200-day avg", a.regime_dependence.above_200sma],
                 ["S&P BELOW its 200-day avg", a.regime_dependence.below_200sma],
                 ["S&P drawdown 5–15% (best)", a.regime_dependence.drawdown_5_15],
                 ["S&P drawdown over 15%", a.regime_dependence.drawdown_over_15]] as const).map(([l, g]) => (
                <tr key={l}>
                  <td style={{ padding: "2px 12px 2px 0", color: "var(--text-dim)" }}>{l}</td>
                  <td style={{ padding: "2px 12px 2px 0", fontFamily: "var(--font-mono)" }}>{g.trades} trades</td>
                  <td style={{ padding: "2px 12px 2px 0", fontFamily: "var(--font-mono)" }}>{g.win_pct}% win</td>
                  <td style={{ padding: "2px 0", fontFamily: "var(--font-mono)", fontWeight: 700,
                               color: g.mean_pct > 0 ? TONE.ok : TONE.bad }}>
                    {g.mean_pct > 0 ? "+" : ""}{g.mean_pct}%/trade
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 4 }}>
            Only 8% of the tested history sits in the losing regime, so the headline above describes
            a market that mostly went up. Ordinary pullbacks are its sweet spot; real breaks are not.
          </div>
        </div>
      )}

      {a.count === 0 ? (
        /* An empty list used to ASSERT "the screen working, not the screen
           broken" and show nothing to back it up. That reads the same whether
           the scan covered the universe and found nothing or died after three
           symbols, which is why the owner could not tell working from broken.
           Show the measurement instead: how many names were evaluated, how far
           off the closest were, and which half of the rule stopped them. */
        <div style={{ padding: 16, border: "1px dashed var(--border)", borderRadius: 8,
                      color: "var(--text-dim)", fontSize: 15 }}>
          <b>No candidates right now.</b>{" "}
          {typeof a.evaluated === "number"
            ? <>The rule was evaluated against <b>{a.evaluated}</b> symbols on the {a.signal_bar} close
               and none cleared it.</>
            : <>The screen is deliberately selective — roughly 1–2 signals a day.</>}{" "}
          It fires only on a 2.5σ dip in a name still above its 200-day average.

          {a.near_misses && a.near_misses.length > 0 && (
            <div style={{ marginTop: 14 }}>
              {/* Two DIFFERENT reasons, and calling both "closest to firing" was
                  wrong: BC at -2.67 and NEE at -2.61 have ALREADY cleared the
                  2.5σ test. Telling the reader they "need σ below −2.5" about a
                  name that is at −2.67 is a screen contradicting itself. They
                  are not close to firing — they fired on σ and were refused on
                  trend. Split, and each group labelled for its own reason. */}
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-dim)", marginBottom: 6 }}>
                {a.near_misses.some((n) => n.sigma_from_mean <= n.sigma_needed && !n.above_trend)
                  ? "Refused, and how far off the rest are"
                  : "Closest to firing — entry needs σ below −2.5"}
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--text-muted)" }}>
                      {["Symbol", "σ from mean", "Close", "Why not"].map((x) => (
                        <th key={x} style={{ padding: "5px 8px", fontWeight: 600, whiteSpace: "nowrap" }}>{x}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {a.near_misses.slice(0, 8).map((n) => (
                      <tr key={n.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                        <td style={{ padding: "5px 8px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{n.symbol}</td>
                        <td style={{ padding: "5px 8px", fontFamily: "var(--font-mono)",
                                     color: n.above_trend ? TONE.warn : TONE.dim }}>
                          {n.sigma_from_mean.toFixed(2)}
                        </td>
                        <td style={{ padding: "5px 8px", fontFamily: "var(--font-mono)" }}>{n.close.toFixed(2)}</td>
                        <td style={{ padding: "5px 8px", color: "var(--text-muted)" }}>
                          {n.sigma_from_mean <= n.sigma_needed && !n.above_trend
                            ? "σ MET — refused on trend (below its 200-SMA)"
                            : n.blocked_by}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{ marginTop: 8, fontSize: 12.5, color: "var(--text-muted)" }}>
                Names below their 200-SMA are refused by the rule. Measured 29 Aug, that filter is
                NEUTRAL, not protective: refused signals earned +1.06%/trade against the rule&rsquo;s
                +1.10% on 3,134 trades, and in the Feb–Apr 2020 crash they did <i>better</i>
                (−4.56% vs −7.85%). The filter stays because REMOVING it passes only 1 of 4
                two-split cells — not because a dip below the 200-day is worthless. It is a
                lower win rate (62% vs 72%) for the same mean: higher variance, not junk.
              </div>
            </div>
          )}
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Tier", "Entry", "Now", "Target", "Stop", "Upside", "R:R", "Depth", "ATR%", "vs 200-SMA"].map((x) => (
                  <th key={x} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {a.candidates.map((c) => (
                <tr key={c.symbol} style={{ borderTop: "1px solid #141b2b" }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{c.symbol}</td>
                  <td style={{ padding: "8px 10px" }}>
                    <span style={{ fontSize: 12, fontWeight: 700, padding: "2px 7px", borderRadius: 999,
                                   color: c.tier === "core" ? TONE.ok : TONE.warn,
                                   border: `1px solid ${(c.tier === "core" ? TONE.ok : TONE.warn)}55` }}>
                      {c.tier}
                    </span>
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>{c.entry_hint.toFixed(2)}</td>
                  {/* LATEST PRICE, from the 5-minute lane. Owner, repeatedly:
                      "I need latest prices." The screen quoted Friday's 29.71
                      while HPQ traded at 28.58 — a 3.7% gap between the number
                      shown and the number you would pay. The SIGNAL still comes
                      from the settled bar; this is so the plan is not silently
                      stale. */}
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>
                    {c.latest ? (
                      <>
                        <b style={{ color: c.latest.price < c.entry_hint ? TONE.bad : TONE.ok }}>
                          {c.latest.price.toFixed(2)}
                        </b>
                        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
                          {" "}({(100 * (c.latest.price / c.entry_hint - 1)).toFixed(1)}%)
                        </span>
                        <div style={{ color: "var(--text-muted)", fontSize: 10 }}>
                          {c.latest.as_of.slice(11, 16)} UTC
                        </div>
                      </>
                    ) : <span style={{ color: "var(--text-muted)" }}>—</span>}
                  </td>
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
                      borderRadius: 8, padding: "8px 12px", fontSize: 14, lineHeight: 1.6 }}>
          <b style={{ color: TONE.warn }}>
            {a.quarantined.length} symbol{a.quarantined.length === 1 ? "" : "s"} dropped — suspect price history
          </b>
          <div style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 3 }}>
            These carry a stored series that looks like a different instrument (wrong venue or wrong
            contract). It passes NaN/spike checks because the series is internally consistent — it is
            simply not this security. Mean reversion is the strategy most exposed to it: a wrong-venue
            series looks permanently, enormously cheap.
          </div>
          <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 13 }}>
            {a.quarantined.map((q) => (
              <div key={q.symbol}><b>{q.symbol}</b> — {q.detail}</div>
            ))}
          </div>
        </div>
      )}

      {/* Limits stated on the surface, not in a doc nobody opens. */}
      <div style={{ marginTop: 12, fontSize: 13, color: "var(--text-muted)", lineHeight: 1.6 }}>
        <b>What this does not do:</b>
        <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
          {a.limits.map((x, i) => <li key={i}>{x}</li>)}
          <li>38% of these lose. The edge is the average across many, not any single row.</li>
        </ul>
      </div>
    </div>
  );
}
