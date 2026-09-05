import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { OptionPositionsCard } from "./OptionPositionsCard";

/**
 * Index-strangle decision history — what was decided each day, and WHY.
 *
 * Owner, 31 Aug 2026: "i need to be able to see these decisions for the daily
 * ones so i can ask another agent to verify how we doing with this strategy",
 * and "MCP as well as another screen".
 *
 * STAND-ASIDES ARE SHOWN, and they are the point. The edge of this strategy is
 * what the volatility gate REFUSES to trade. A screen of only the trades would
 * be a highlight reel and could not answer whether the gate is set correctly —
 * which is the question anyone reviewing this is actually being asked.
 *
 * PROVISIONAL rows are marked. Before a session opens there is no opening
 * price, so the strikes are priced off the previous close: a real decision, but
 * not a placeable trade. On 31 Aug that distinction mattered — NIFTY moved 110
 * points overnight and the pre-open strikes were badly lopsided by the time
 * they could have been placed.
 */
type Row = {
  market: string; as_of: string; decided_at_utc: string;
  decision: string; reason: string;
  vol_symbol: string | null; vol_index: number | null; vol_threshold: number | null;
  spot: number | null; spot_basis: string | null; provisional: boolean;
  session_state: string | null; expiry_kind: string | null; dte: number | null;
  put_strike: number | null; call_strike: number | null;
  outcome_pct: number | null; graded_at_utc: string | null;
  forward: number | null; vol_at_decision: number | null;
  data_source: string | null; exchange_date: string | null;
  // EXECUTION — what actually happened, not just what was decided. Recorded
  // since migration 072; until then the platform could not answer "did the
  // strangle work or not" from its own records.
  placed: boolean | null; partial: boolean | null; shadow: boolean | null;
  broker_order_ids: string | null; credit_actual: number | null;
  credit_modelled: number | null; realised_pnl: number | null;
  close_trigger: string | null; closed_at_utc: string | null;
  exit_cost_actual: number | null; lot: number | null;
};
type Summary = {
  market: string; evaluated: number; traded: number; declined: number;
  provisional: number; graded: number; mean_outcome_pct: number | null;
};

const TONE = { ok: "#0f8a5f", off: "#8b95a5", warn: "#d29922", bad: "#f85149" };

/** A live option leg at the broker — the only place a FILL PRICE exists. */
type Leg = {
  instrumentName: string | null; ticker: string | null; quantity: number;
  averagePricePaid: number | null; currentPrice: number | null;
  unrealisedAbs: number | null; multiplier: number | null; isOption?: boolean;
};

export function StrangleDecisionsView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [sum, setSum] = useState<Summary[]>([]);
  const [legs, setLegs] = useState<Leg[]>([]);
  const [legErr, setLegErr] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        api.strangleDecisions(days) as Promise<{ rows: Row[] }>,
        api.strangleDecisionSummary(days) as Promise<{ rows: Summary[] }>,
      ]);
      setRows(d.rows || []); setSum(s.rows || []); setErr(null);
    } catch (e) { setErr(String((e as Error)?.message || e)); }
    // LIVE LEGS, separately — a broker hiccup must not blank the history.
    try {
      const p = await api.ibkrPositions();
      setLegs((p.positions ?? []).filter((x) => x.isOption) as Leg[]);
      setLegErr(p.error ?? null);
    } catch (e) { setLegErr(String((e as Error)?.message || e)); }
  }, [days]);

  useEffect(() => { void load(); }, [load]);
  // Open positions move; the decision history does not. Re-poll while open.
  useEffect(() => {
    const t = setInterval(() => void load(), 60_000);
    return () => clearInterval(t);
  }, [load]);

  if (err) return <div style={{ padding: 16, color: TONE.bad }}>Unavailable: {err}</div>;

  return (
    <div style={{ padding: 16 }}>
      {/* What is actually OPEN comes first. A decision log is history; a live
          short position is money at risk right now. */}
      <div style={{ marginBottom: 16 }}><OptionPositionsCard /></div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Strangle decisions</h2>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          every evaluation, including the days we stood aside
        </span>
        <span style={{ marginLeft: "auto" }}>
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)}
              style={{ marginLeft: 6, padding: "3px 9px", borderRadius: 5, fontSize: 12,
                       cursor: "pointer", color: "var(--text)",
                       background: d === days ? "var(--surface-2)" : "transparent",
                       border: "1px solid var(--border)" }}>{d}d</button>
          ))}
        </span>
      </div>

      {/* Traded and DECLINED side by side — the gate is the strategy, so a
          tally of only the trades cannot show whether it is set right. */}
      <table style={{ width: "100%", borderCollapse: "collapse", margin: "14px 0", fontSize: 13 }}>
        <thead><tr style={{ color: "var(--text-muted)", textAlign: "left" }}>
          <th style={{ padding: "6px 8px" }}>Market</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Evaluated</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Traded</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Declined</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Provisional</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Graded</th>
        </tr></thead>
        <tbody>
          {sum.map((s) => (
            <tr key={s.market} style={{ borderTop: "1px solid var(--border)" }}>
              <td style={{ padding: "6px 8px", fontWeight: 600 }}>{s.market}</td>
              <td style={{ padding: "6px 8px", textAlign: "right" }}>{s.evaluated}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: TONE.ok }}>{s.traded}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: TONE.off }}>{s.declined}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: s.provisional ? TONE.warn : "inherit" }}>{s.provisional}</td>
              <td style={{ padding: "6px 8px", textAlign: "right" }}>{s.graded}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* WHAT IS OPEN RIGHT NOW, AT WHAT PRICE, AND WHAT IT IS WORTH.
          Owner, 4 Sep 2026: "i do not know what price it was executed, whats
          the live pnl etc". The screen showed what was DECIDED and nothing
          about what was DONE — and the fill price lives ONLY on the broker
          position, because IBKR returns avgPrice null on the order itself. */}
      <div style={{ border: "1px solid var(--border)", borderRadius: 10,
                    padding: 14, margin: "14px 0" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
          <span style={{ fontWeight: 600 }}>Open now — at the broker</span>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            live P&amp;L · refreshes every 60s
          </span>
          {legs.length > 0 && (() => {
            const net = legs.reduce((a, l) => a + (l.unrealisedAbs || 0), 0);
            const credit = legs.reduce(
              (a, l) => a + (l.averagePricePaid || 0) * Math.abs(l.quantity) * (l.multiplier || 100), 0);
            return (
              <span style={{ marginLeft: "auto", display: "flex", gap: 14, alignItems: "baseline" }}>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  credit collected <b style={{ color: "var(--text)" }}>{credit.toFixed(2)}</b>
                </span>
                <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums",
                               color: net >= 0 ? TONE.ok : TONE.bad }}>
                  {net >= 0 ? "+" : ""}{net.toFixed(2)}
                </span>
              </span>
            );
          })()}
        </div>

        {legErr ? (
          <div style={{ fontSize: 12.5, color: TONE.warn }}>
            Broker unreadable: {legErr}
          </div>
        ) : legs.length === 0 ? (
          <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
            Flat — no option legs open.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5,
                          fontVariantNumeric: "tabular-nums" }}>
            <thead><tr style={{ color: "var(--text-muted)", textAlign: "left", fontSize: 11 }}>
              <th style={{ padding: "5px 6px" }}>Contract</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Qty</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>SOLD AT</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Now</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Credit</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Live P&amp;L</th>
            </tr></thead>
            <tbody>
              {legs.map((l, i) => {
                const mult = l.multiplier || 100;
                const credit = (l.averagePricePaid || 0) * Math.abs(l.quantity) * mult;
                const pnl = l.unrealisedAbs ?? 0;
                return (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "6px" }}>
                      {l.instrumentName || l.ticker}
                      {l.quantity < 0 && (
                        <span style={{ fontSize: 9, marginLeft: 5, color: "var(--text-muted)" }}>SHORT</span>
                      )}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right" }}>{l.quantity}</td>
                    <td style={{ padding: "6px", textAlign: "right", fontWeight: 600 }}>
                      {l.averagePricePaid?.toFixed(4) ?? "—"}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right" }}>
                      {l.currentPrice?.toFixed(4) ?? "—"}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right", color: "var(--text-muted)" }}>
                      {credit.toFixed(2)}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right", fontWeight: 600,
                                 color: pnl >= 0 ? TONE.ok : TONE.bad }}>
                      {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>
          <b style={{ color: "var(--text)" }}>SOLD AT</b> is the price the broker actually filled —
          per share. <b style={{ color: "var(--text)" }}>Credit</b> is that × quantity × multiplier,
          i.e. the money received. These are short, so a <b>falling</b> price is a gain.
        </div>
      </div>

      {/* GATED vs OVERRIDDEN, side by side. This is the comparison the whole
          design exists to make: the strategy's edge is what the gate REFUSES,
          so the only way to know whether the threshold is set right is to
          trade some refused days on purpose and keep the two populations
          apart. Averaging them would destroy the very measurement. */}
      {(() => {
        const done = rows.filter((r) => r.placed === true && r.realised_pnl != null);
        const gated = done.filter((r) => !r.shadow);
        const over = done.filter((r) => r.shadow);
        const sumOf = (xs: Row[]) => xs.reduce((a, r) => a + (r.realised_pnl || 0), 0);
        const openNow = rows.filter((r) => r.placed === true && r.realised_pnl == null).length;
        if (!done.length && !openNow) return null;
        const cell = (label: string, n: number, pnl: number | null, note: string) => (
          <div style={{ flex: 1, minWidth: 190, border: "1px solid var(--border)",
                        borderRadius: 8, padding: "10px 12px" }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 3 }}>
              <span style={{ fontSize: 20, fontWeight: 600 }}>{n}</span>
              {pnl != null && (
                <span style={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: "tabular-nums",
                               color: pnl >= 0 ? TONE.ok : TONE.bad }}>
                  {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, lineHeight: 1.45 }}>
              {note}
            </div>
          </div>
        );
        return (
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "16px 0 8px" }}>
            {cell("Gate said trade — closed", gated.length, gated.length ? sumOf(gated) : null,
                  "the strategy as designed")}
            {cell("Gate REFUSED — traded anyway", over.length, over.length ? sumOf(over) : null,
                  "captured on purpose. A LOSS here is evidence the gate is set right")}
            {cell("Still open", openNow, null, "P&L lands when the position closes")}
          </div>
        );
      })()}

      {/* WHAT WE ACTUALLY DID, and whether it agreed with the gate.
          Owner, 1 Sep 2026: "i shd be able to see these executions on screen
          on daily basis and pnl and also if gate quality was overriden". */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5,
                      fontVariantNumeric: "tabular-nums" }}>
        <thead><tr style={{ color: "var(--text-muted)", textAlign: "left", fontSize: 11 }}>
          <th style={{ padding: "6px 8px" }}>Date</th>
          <th style={{ padding: "6px 8px" }}>Market</th>
          <th style={{ padding: "6px 8px" }}>Gate said</th>
          <th style={{ padding: "6px 8px" }}>We did</th>
          <th style={{ padding: "6px 8px" }}>Vol vs gate</th>
          <th style={{ padding: "6px 8px" }}>Strikes</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>Credit</th>
          <th style={{ padding: "6px 8px", textAlign: "right" }}>P&amp;L</th>
          <th style={{ padding: "6px 8px" }}>Exit</th>
        </tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const gateOpened = r.decision === "CANDIDATE";
            // THE OVERRIDE. shadow means the gate REFUSED and we placed anyway
            // to capture execution. It is tinted and worded, never colour
            // alone, because it is the row a reviewer must not miss: a LOSING
            // override is evidence the gate is set RIGHT.
            const override = r.placed === true && r.shadow === true;
            const pnl = r.realised_pnl;
            return (
              <tr key={i} style={{ borderTop: "1px solid var(--border)",
                                   background: override ? "rgba(210,153,34,.07)" : undefined }}>
                <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>
                  {String(r.exchange_date || r.as_of).slice(0, 10)}
                </td>
                <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                  {r.market}
                  {r.expiry_kind && (
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}> {r.expiry_kind}</span>
                  )}
                </td>
                <td style={{ padding: "6px 8px", whiteSpace: "nowrap",
                             color: gateOpened ? TONE.ok : TONE.off }}>
                  {gateOpened ? "trade" : "stand aside"}
                </td>
                <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>
                  {r.placed === true ? (
                    override
                      ? <b style={{ color: TONE.warn }}>OVERRODE — placed anyway</b>
                      : <span style={{ color: TONE.ok }}>placed</span>
                  ) : r.placed === false
                    ? <span style={{ color: TONE.off }}>not placed</span>
                    : (r.realised_pnl != null || r.close_trigger)
                      // TRADED, BUT THE PLACEMENT WAS NEVER LINKED. A realised
                      // P&L cannot exist without a position, so "—" here is a
                      // gap in OUR record, not an absence of a trade.
                      //
                      // 4 Sep 2026: SPX showed "—" beside +123.89. The
                      // placement link was 404ing (it sent as_of while the
                      // endpoint keyed on exchange_date) so `placed` stayed
                      // null while the exit recorded fine. Rendering that as
                      // "—" invited the reader to conclude nothing happened.
                      ? <b style={{ color: TONE.bad }} title="A realised P&L means a position existed. The placement record is missing, not the trade.">
                          traded &mdash; PLACEMENT NOT RECORDED
                        </b>
                      : <span style={{ color: TONE.off }}>&mdash;</span>}
                  {r.partial && <b style={{ color: TONE.bad }}> · PARTIAL (naked)</b>}
                </td>
                <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                  {r.vol_index ?? "—"} / {r.vol_threshold ?? "—"}
                </td>
                <td style={{ padding: "6px 8px", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>
                  {r.put_strike ? `${r.put_strike.toLocaleString()} / ${r.call_strike?.toLocaleString()}` : "—"}
                </td>
                <td style={{ padding: "6px 8px", textAlign: "right", whiteSpace: "nowrap" }}>
                  {/* A TRADED credit and a MODELLED one are different things and
                      are never printed as the same number. */}
                  {r.credit_actual != null
                    ? <b>{r.credit_actual.toFixed(2)}</b>
                    : r.credit_modelled != null
                      ? <span style={{ color: TONE.off }}>
                          {r.credit_modelled.toFixed(0)}<i style={{ fontSize: 10 }}> modelled</i>
                        </span>
                      : "—"}
                </td>
                <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600,
                             color: pnl == null ? "var(--text-muted)" : pnl >= 0 ? TONE.ok : TONE.bad }}>
                  {pnl == null ? "—" : `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`}
                </td>
                <td style={{ padding: "6px 8px", fontSize: 11, color: "var(--text-muted)",
                             whiteSpace: "nowrap" }}>
                  {r.close_trigger || (r.placed ? "open" : "—")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div style={{ marginTop: 14, fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
        <b style={{ color: "var(--text)" }}>Stood-aside rows are the point.</b> The edge of this
        strategy is what the volatility gate refuses; a list of only the trades could not tell you
        whether the gate is set correctly.
        <br />
        <b style={{ color: "var(--text)" }}>Provisional</b> means the strikes were priced off the
        previous close because the session had not opened — a real decision, but not a placeable
        trade.
        <br />
        <b style={{ color: TONE.warn }}>OVERRODE</b> means the volatility gate said stand aside and
        the trade was placed regardless, to capture a real fill. Those rows are tinted and are
        counted <b>separately</b> — a losing override is evidence the gate is set correctly, and
        blending it into the gated numbers would destroy that measurement.
        <br />
        <b style={{ color: TONE.bad }}>traded — PLACEMENT NOT RECORDED</b> means a realised P&amp;L
        exists with no placement row. The trade happened; our record of opening it did not. Treat
        the P&amp;L as real and the row as incomplete.
        <br />
        <b style={{ color: "var(--text)" }}>Credit</b> in bold is what the broker actually filled.
        Grey <i>modelled</i> is Black-Scholes off a volatility index — no skew, no bid-ask, and not
        a price anyone was offered. The two are never shown as the same number.
        <br />
        <b style={{ color: "var(--text)" }}>Outcomes are ungraded</b> until after the session closes.
        Grading a decision before then would be the same lookahead this strategy has already had to
        be corrected for.
      </div>
    </div>
  );
}
