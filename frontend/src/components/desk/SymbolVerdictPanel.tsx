/**
 * SymbolVerdictPanel — "what do OUR strategies say about this symbol, today?"
 *
 * Owner: "what if I want to see the probability on a particular symbol — will
 * I just put that symbol in odds and it will run all the strategy with latest
 * data for it."
 *
 * It did not, and that was a fair thing to expect. Odds answered a narrower
 * question — "if I place THIS order, how often did it work" — and never asked
 * what the strategies we actually trade think of the name.
 *
 * WHY IT SHOWS "NO" IN DETAIL. On any given day almost every symbol fails
 * almost every rule; Swing produced ZERO candidates across 244 names today.
 * A bare "no signal" is therefore useless — it is the answer nearly always.
 * What is useful is HOW FAR from firing: a name at 2.3σ is worth looking at
 * tomorrow, one at 0.4σ is not.
 *
 * NO PROBABILITY IS SHOWN FOR A RULE THAT DOES NOT FIRE, deliberately. The
 * historical win rates belong to trades the rule actually took. Quoting "66%"
 * beside a symbol the rule declined would attach real evidence to a trade the
 * evidence never covered.
 */
import { useCallback, useState } from "react";
import { api } from "../../api/client";
import { checkSwing, checkMomentum, type Bar, type RuleCheck } from "../../lib/tradeOdds";

const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30" };

function Rule({ name, check, evidence }:
              { name: string; check: RuleCheck | null; evidence: string }) {
  if (!check) return (
    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
      {name}: not enough stored history to evaluate (needs 210 sessions).
    </div>
  );
  return (
    <div style={{ border: `1px solid ${check.fires ? TONE.ok : "var(--border)"}${check.fires ? "77" : ""}`,
                  background: check.fires ? `${TONE.ok}0e` : "transparent",
                  borderRadius: 8, padding: "10px 12px", marginBottom: 10 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>{name}</b>
        <span style={{ fontSize: 12, fontWeight: 700, color: check.fires ? TONE.ok : "var(--text-muted)" }}>
          {check.headline}
        </span>
      </div>
      <table style={{ borderCollapse: "collapse", fontSize: 11, marginTop: 6, width: "100%" }}>
        <tbody>
          {check.clauses.map((c, i) => (
            <tr key={i} style={{ borderTop: i ? "1px solid #141b2b" : undefined }}>
              <td style={{ padding: "3px 8px 3px 0", width: 16,
                           color: c.ok ? TONE.ok : TONE.bad }}>{c.ok ? "✓" : "✗"}</td>
              <td style={{ padding: "3px 0", color: c.ok ? "inherit" : "var(--text-muted)" }}>{c.label}</td>
              <td style={{ padding: "3px 0", textAlign: "right", fontFamily: "var(--font-mono)",
                           fontWeight: 700 }}>{c.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {check.fires && check.plan && (
        <div style={{ marginTop: 6, fontSize: 12, fontFamily: "var(--font-mono)" }}>
          entry <b>{check.plan.entry.toFixed(2)}</b>
          {check.plan.target !== undefined && <> · target <b style={{ color: TONE.ok }}>
            {check.plan.target.toFixed(2)}</b> (+{check.plan.targetPct?.toFixed(1)}%)</>}
          {" "}· stop <b style={{ color: TONE.bad }}>{check.plan.stop.toFixed(2)}</b>
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 5, lineHeight: 1.5 }}>
        {check.fires ? evidence
          : "No probability is shown, deliberately — the historical win rate belongs to "
            + "trades this rule actually took. Quoting it beside a symbol the rule declined "
            + "would attach real evidence to a trade it never covered."}
      </div>
    </div>
  );
}

export function SymbolVerdictPanel() {
  const [sym, setSym] = useState("MU");
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(async () => {
    setBusy(true); setErr(null); setBars(null);
    try {
      const r = await api.ibkrBars({ symbol: sym.toUpperCase(), resolution: "1d", limit: 6000 });
      const b = (r.bars ?? []).filter((x) => x.high >= x.low && x.close > 0)
        .map((x) => ({ ts: x.ts, open: x.open, high: x.high, low: x.low, close: x.close }));
      if (!b.length) { setErr(`No stored daily bars for ${sym.toUpperCase()}.`); return; }
      setBars(b); setAsOf(b[b.length - 1].ts.slice(0, 10));
    } catch (e) { setErr(String((e as Error)?.message || e)); }
    finally { setBusy(false); }
  }, [sym]);

  const swing = bars ? checkSwing(bars) : null;
  const mom = bars ? checkMomentum(bars) : null;
  const inp = { background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 6,
                padding: "6px 8px", color: "inherit", fontFamily: "var(--font-mono)" } as const;

  return (
    <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>What do our strategies say about one symbol?</h3>
      <div style={{ fontSize: 12, color: "var(--text-dim)", margin: "6px 0 10px", lineHeight: 1.6 }}>
        Runs the live Swing and Momentum rules against the latest settled bars for any symbol —
        including the ones the screens did not surface, and showing <b>how far</b> from firing each
        rule is rather than a bare no.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", marginBottom: 12 }}>
        <input value={sym} onChange={(e) => setSym(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") run(); }}
               style={{ ...inp, width: 110, textTransform: "uppercase" }} />
        <button onClick={run} disabled={busy}
                style={{ ...inp, cursor: "pointer", fontWeight: 700 }}>
          {busy ? "Checking…" : "Check"}
        </button>
        {asOf && <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          last settled bar {asOf} · {bars?.length.toLocaleString()} sessions
        </span>}
      </div>
      {err && <div style={{ color: TONE.bad, fontSize: 12, marginBottom: 10 }}>{err}</div>}
      {bars && (
        <>
          <Rule name="Swing (mean reversion)" check={swing}
                evidence="2,251 trades · 64.9% win · +0.85%/trade · worst −17.7% · median hold 7 sessions.
                          Live baseline is +0.77% — entering at the next open rather than the signal
                          close costs about 10% of the edge." />
          <Rule name="Momentum (pullback to the 10-day average)" check={mom}
                evidence="5,396 trades · 48.8% win · +2.20%/trade · MEDIAN −0.33% · worst −29.7% ·
                          median hold 35 sessions. The typical trade loses; the profit is in the tail." />
          <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6 }}>
            Computed from stored daily bars up to the last settled session — not live prices. A rule
            firing here is not an instruction: Swing is in a 12-week paper forward test and nothing
            has yet been validated against a broker.
          </div>
        </>
      )}
    </div>
  );
}
