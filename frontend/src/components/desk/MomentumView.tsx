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

type Cand = Resp["artifact"]["candidates"][number];

/**
 * The row detail. Deliberately NOT chart-first — the owner's standing note is
 * "looking at graph adds me no value" — so it leads with the two questions a
 * chart cannot answer: why is this row here, and has this rule ever worked on
 * THIS symbol.
 *
 * The per-symbol record is the part that earns its place. MDB qualifies on
 * every clause of the entry today and this rule has lost money on MDB in 6 of
 * its last 7 attempts. The universe-wide 47%/+1.53% is true and would have
 * hidden that completely.
 */
function RowDetail({ c }: { c: Cand }) {
  const h = c.history;
  const cell = { padding: "4px 10px", fontFamily: "var(--font-mono)" } as const;
  return (
    <tr>
      <td colSpan={8} style={{ padding: "12px 14px 16px", background: "rgba(255,255,255,0.02)",
                               borderTop: "1px solid #141b2b" }}>
        <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))" }}>

          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)", marginBottom: 6 }}>
              WHY {c.symbol} IS ON THIS LIST
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <tbody>
                {c.checks?.map((k, i) => (
                  <tr key={i} style={{ borderTop: i ? "1px solid #141b2b" : undefined }}>
                    <td style={{ padding: "4px 8px 4px 0", color: TONE.ok, width: 14 }}>✓</td>
                    <td style={{ padding: "4px 0" }}>
                      {k.label}
                      <div style={{ color: "var(--text-muted)", fontSize: 10, fontFamily: "var(--font-mono)" }}>
                        {k.detail}
                      </div>
                    </td>
                    <td style={{ ...cell, textAlign: "right", fontWeight: 700, whiteSpace: "nowrap" }}>{k.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)", lineHeight: 1.6 }}>
              Moving averages: 10 <b>{c.levels?.sma10}</b> · 20 <b>{c.levels?.sma20}</b> ·
              50 <b>{c.levels?.sma50}</b> · 200 <b>{c.levels?.sma200}</b>.
              The 10-day average is the support this trade is buying — see the note below the table.
            </div>
          </div>

          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-dim)", marginBottom: 6 }}>
              THIS RULE&apos;S RECORD ON {c.symbol}
            </div>
            {!h ? (
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                This rule has never completed a trade on {c.symbol} in the stored history.
                Today would be the first. The universe-wide evidence is all you have here.
              </div>
            ) : (
              <>
                <div style={{ fontSize: 12, lineHeight: 1.7 }}>
                  <b style={{ color: h.mean_pct > 0 ? TONE.ok : TONE.bad }}>
                    {h.trades} trades · {h.win_rate_pct}% win · {h.mean_pct > 0 ? "+" : ""}{h.mean_pct}%/trade
                  </b>
                  <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                    best +{h.best_pct}% · worst {h.worst_pct}% · median hold {h.median_bars} sessions
                  </div>
                </div>
                {h.sample_warning && (
                  <div style={{ marginTop: 6, fontSize: 10, color: TONE.warn, lineHeight: 1.5 }}>
                    ⚠ {h.sample_warning}
                  </div>
                )}
                {h.mean_pct < 0 && (
                  <div style={{ marginTop: 6, fontSize: 10, color: TONE.bad, lineHeight: 1.5 }}>
                    This rule has LOST money on {c.symbol} historically. It qualifies today on every
                    clause of the entry — and it has not worked here. That is a reason to skip the row.
                  </div>
                )}
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, marginTop: 8 }}>
                  <thead>
                    <tr style={{ color: "var(--text-dim)", textAlign: "left" }}>
                      {["Entered", "Exited", "Bars", "Why", "Result"].map((x) => (
                        <th key={x} style={{ padding: "3px 6px", fontWeight: 600 }}>{x}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {h.last_5.map((t, i) => (
                      <tr key={i} style={{ borderTop: "1px solid #141b2b" }}>
                        <td style={cell}>{t.entry_date}</td>
                        <td style={cell}>{t.exit_date}</td>
                        <td style={cell}>{t.bars}</td>
                        <td style={{ ...cell, color: "var(--text-muted)" }}>{t.exit}</td>
                        <td style={{ ...cell, fontWeight: 700, color: t.pct > 0 ? TONE.ok : TONE.bad }}>
                          {t.pct > 0 ? "+" : ""}{t.pct}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </div>

        {/* SUPPORT AND RESISTANCE — answered here rather than left implicit,
            because it is the first thing anyone asks of a screen like this. */}
        <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px solid #141b2b",
                      fontSize: 10, color: "var(--text-muted)", lineHeight: 1.6 }}>
          <b>On support &amp; resistance:</b> drawn S/R levels are deliberately NOT used. We tested
          them — 76,260 touch events — and both edges came out NEGATIVE against a placebo of random
          lines. They are in the Research view as a failed study. What this rule uses instead is
          DYNAMIC support: the 10-day average, which is the one "buy the dip to support" formulation
          that cleared its gates. The stop is a fixed −8% and then an 8% trail, never a level.
        </div>
      </td>
    </tr>
  );
}

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };
type Resp = Awaited<ReturnType<typeof api.momentumCandidates>>;

export function MomentumView() {
  const [d, setD] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
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
                <>
                <tr key={c.symbol}
                    onClick={() => setOpen(open === c.symbol ? null : c.symbol)}
                    title={`Open ${c.symbol} — why it qualified, and how this rule has done on it`}
                    style={{ borderTop: "1px solid #141b2b", cursor: "pointer",
                             background: open === c.symbol ? "rgba(255,255,255,0.03)" : undefined }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    <span style={{ color: "var(--text-dim)", marginRight: 6, fontSize: 9 }}>
                      {open === c.symbol ? "▼" : "▶"}
                    </span>
                    {c.symbol}
                    {c.history && c.history.mean_pct < 0 && (
                      <span title="This rule has lost money on this symbol historically — open the row"
                            style={{ marginLeft: 6, color: TONE.bad, fontSize: 10 }}>⚠</span>
                    )}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 700 }}>{c.entry_hint.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.bad }}>{c.stop.toFixed(2)}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>{c.trailing_pct.toFixed(0)}% off peak</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: TONE.ok }}>+{c.pct_above_200sma.toFixed(1)}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>+{c.pct_above_20sma.toFixed(1)}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{c.atr_pct?.toFixed(1) ?? "—"}%</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{c.off_52w_high_pct?.toFixed(1) ?? "—"}%</td>
                </tr>
                {open === c.symbol && <RowDetail c={c} />}
                </>
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
