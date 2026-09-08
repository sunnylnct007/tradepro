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
  place_error: string | null;
};
type Summary = {
  market: string; evaluated: number; traded: number; declined: number;
  provisional: number; graded: number; mean_outcome_pct: number | null;
};

const TONE = { ok: "#0f8a5f", off: "#8b95a5", warn: "#d29922", bad: "#f85149" };

/** A live option leg at the broker — the only place a FILL PRICE exists. */
type Leg = {
  conid: number;
  instrumentName: string | null; ticker: string | null; quantity: number;
  averagePricePaid: number | null; currentPrice: number | null;
  unrealisedAbs: number | null; multiplier: number | null; isOption?: boolean;
};

type Pop = {
  label: string; n: number;
  total: number | null; mean: number | null; best: number | null; worst: number | null;
  wins: number; losses: number; scratches: number;
  winRate: number | null; winRateWithheld: string | null;
};
type PnlLeg = {
  contract: string; conid: number; market: string | null; quantity: number;
  soldAt: number | null; markedAt: number | null; unrealised: number;
  placedAtUtc: string | null; heldMinutes: number | null; whyNoTime: string | null;
};
type PnlTrade = {
  market: string; shadow: boolean;
  placedAtUtc: string | null; closedAtUtc: string | null; heldMinutes: number | null;
  credit: number | null; realised: number | null;
};
type Pnl = {
  asOfUtc: string; broker: string | null; total: number | null;
  realised: { total: number; pairs: number; trades: PnlTrade[] };
  open: { markedAtUtc: string; unrealised: number | null; legs: number;
          detail: PnlLeg[]; unmarkable: { contract: string }[] };
  warnings: string[];
};

type Stats = {
  windowDays: number;
  automated: {
    currency: string; account: string; closed: number; sessions: number;
    gated: Pop; shadow: Pop;
    byTrigger: { trigger: string; n: number; total: number }[];
    byMarket: { market: string; n: number; total: number; shadowOnly: boolean }[];
  };
  manual: {
    closed: number;
    byCurrency: { currency: string; stats: Pop; followedSignal: Pop; ignoredSignal: Pop }[];
  };
  caveats: string[];
};

export function StrangleDecisionsView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [sum, setSum] = useState<Summary[]>([]);
  const [legs, setLegs] = useState<Leg[]>([]);
  const [legErr, setLegErr] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [stats, setStats] = useState<Stats | null>(null);
  const [pnl, setPnl] = useState<Pnl | null>(null);
  // When the browser last got a reply. The server timestamp says when the mark
  // was taken; this says how stale the copy on screen is. They are different
  // questions and a P&L needs both answered.
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        api.strangleDecisions(days) as Promise<{ rows: Row[] }>,
        api.strangleDecisionSummary(days) as Promise<{ rows: Summary[] }>,
      ]);
      setRows(d.rows || []); setSum(s.rows || []); setErr(null);
    } catch (e) { setErr(String((e as Error)?.message || e)); }
    // Stats separately — a stats failure must not blank the history either.
    try { setStats((await api.strangleStats(days)) as unknown as Stats); }
    catch { setStats(null); }
    try {
      setPnl((await api.strangleLivePnl(days)) as unknown as Pnl);
      setFetchedAt(new Date());
    } catch { setPnl(null); }
    // LIVE LEGS, separately — a broker hiccup must not blank the history.
    try {
      const p = await api.ibkrPositions();
      setLegs((p.positions ?? []).filter((x) => x.isOption) as Leg[]);
      setLegErr(p.error ?? null);
    } catch (e) { setLegErr(String((e as Error)?.message || e)); }
  }, [days]);

  useEffect(() => { void load(); }, [load]);
  // "18s ago" must keep counting between the 60s reloads, or the screen shows a
  // freshness claim that was true once and silently stopped being true.
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  // Open positions move; the decision history does not. Re-poll while open.
  useEffect(() => {
    const t = setInterval(() => void load(), 60_000);
    return () => clearInterval(t);
  }, [load]);

  if (err) return <div style={{ padding: 16, color: TONE.bad }}>Unavailable: {err}</div>;

  // ── formatting helpers ───────────────────────────────────────────────
  // UTC, always, and labelled Z. This desk spans New York, London and Mumbai;
  // a bare "14:00" is ambiguous across all three and the exchange calendar is
  // the thing being reasoned about, not the reader's wall clock.
  const hhmmss = (iso: string | null | undefined) =>
    iso ? `${new Date(iso).toISOString().slice(11, 19)}Z` : "—";
  const held = (mins: number | null | undefined) => {
    if (mins == null) return "—";
    const m = Math.round(mins);
    return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  };
  const ago = (d: Date | null) => {
    if (!d) return "—";
    const sec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
    return sec < 60 ? `${sec}s ago` : `${Math.floor(sec / 60)}m ${sec % 60}s ago`;
  };
  const signed = (v: number | null | undefined, dp = 2) =>
    v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(dp)}`;
  void tick; // re-render each second so `ago` stays true
  // Aliased: inside the leg table `pnl` is that ROW's P&L, and a
  // shadowed name there would silently read the wrong object.
  const pnl0 = pnl;

  return (
    <div style={{ padding: 16 }}>
      {/* THE ONE NUMBER, AND WHEN IT WAS TRUE.
          Owner, 8 Sep 2026: "we need to provide timings as well when we placed
          it, what time is pnl based on etc."

          A P&L with no timestamp is a number of unknown age, and this one moves
          every second the market is open. Two clocks, because they answer two
          different questions: markedAtUtc is when the BROKER priced the book;
          "ago" is how stale the copy in this browser is. A screen that shows
          only the second can look fresh while quoting an hour-old mark.

          Exactly one hero figure per view, and it uses proportional digits —
          tabular figures give every digit the width of a zero, which makes a
          large standalone number look gappy. Tabular is for the columns below,
          where digits must line up. */}
      {pnl && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 10,
                      padding: "14px 16px", marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 20, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
                Desk P&amp;L{pnl.total == null && " — INCOMPLETE"}
              </div>
              <div style={{ fontSize: 48, lineHeight: 1.05, fontWeight: 600,
                            color: pnl.total == null ? TONE.warn
                                 : pnl.total >= 0 ? TONE.ok : TONE.bad }}>
                {pnl.total == null ? "unknown" : signed(pnl.total)}
              </div>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.8,
                          paddingBottom: 4 }}>
              <div>
                realised <b style={{ color: "var(--text)" }}>{signed(pnl.realised.total)}</b>
                {" over "}{pnl.realised.pairs} closed pair(s)
              </div>
              <div>
                open <b style={{ color: "var(--text)" }}>{signed(pnl.open.unrealised)}</b>
                {" across "}{pnl.open.legs} leg(s)
              </div>
            </div>
            <div style={{ marginLeft: "auto", textAlign: "right", fontSize: 11,
                          color: "var(--text-muted)", lineHeight: 1.8, paddingBottom: 4 }}>
              <div>open half marked <b style={{ color: "var(--text)" }}>
                {hhmmss(pnl.open.markedAtUtc)}</b></div>
              <div>this screen refreshed {ago(fetchedAt)}</div>
              <div>{pnl.broker ?? "broker unreadable"}</div>
            </div>
          </div>
          {pnl.warnings.map((w, i) => (
            <div key={i} style={{ marginTop: 9, fontSize: 12, color: TONE.warn,
                                  border: `1px solid ${TONE.warn}`, borderRadius: 6,
                                  padding: "7px 10px", lineHeight: 1.5 }}>{w}</div>
          ))}
        </div>
      )}
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

      {/* CLOSED TRADES, WITH THE CLOCK ON THEM.
          "+187.45" says nothing about whether it was earned over six hours or
          seven minutes — and on this desk that distinction is exactly the
          difference between a strategy result and the stale_overnight defect
          that flattened fresh positions until 8 Sep 2026. HELD is therefore not
          a nicety here; it is the column that tells you which one you are
          looking at, so it is tinted when the trade lasted under an hour. */}
      {pnl && pnl.realised.trades.length > 0 && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 10,
                      padding: 14, margin: "14px 0" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
            <span style={{ fontWeight: 600 }}>Closed — last {days} day(s)</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              all times UTC · a trade held minutes did not earn its result from decay
            </span>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5,
                          fontVariantNumeric: "tabular-nums" }}>
            <thead><tr style={{ color: "var(--text-muted)", textAlign: "left", fontSize: 11 }}>
              <th style={{ padding: "5px 6px" }}>Market</th>
              <th style={{ padding: "5px 6px" }}>Gate</th>
              <th style={{ padding: "5px 6px" }}>Placed</th>
              <th style={{ padding: "5px 6px" }}>Closed</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Held</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Credit</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Realised</th>
            </tr></thead>
            <tbody>
              {pnl.realised.trades.map((t, i) => {
                const brief = t.heldMinutes != null && t.heldMinutes < 60;
                return (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "6px", fontWeight: 600 }}>{t.market}</td>
                    <td style={{ padding: "6px", fontSize: 11,
                                 color: t.shadow ? TONE.warn : "var(--text-muted)" }}>
                      {t.shadow ? "OVERRODE" : "agreed"}
                    </td>
                    <td style={{ padding: "6px" }}>{hhmmss(t.placedAtUtc)}</td>
                    <td style={{ padding: "6px" }}>{hhmmss(t.closedAtUtc)}</td>
                    <td style={{ padding: "6px", textAlign: "right",
                                 color: brief ? TONE.warn : "inherit",
                                 fontWeight: brief ? 600 : 400 }}
                        title={brief ? "under an hour — check the exit trigger before "
                                     + "reading this as a strategy result" : ""}>
                      {held(t.heldMinutes)}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right" }}>
                      {t.credit == null ? "—" : t.credit.toFixed(2)}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right", fontWeight: 600,
                                 color: (t.realised ?? 0) >= 0 ? TONE.ok : TONE.bad }}>
                      {signed(t.realised)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* DESK STATISTICS — and, at this sample size, mostly what they cannot say.
          Owner, 8 Sep 2026: "we need proper stats."

          The caveats render FIRST and in warning tone on purpose. When every
          closed trade is a shadow fill and three of five exits came from a bug,
          the honest headline is not the total — it is that nothing here measures
          the strategy yet. A stats panel that led with a number would be worse
          than no stats panel. */}
      {stats && (stats.automated.closed > 0 || stats.manual.closed > 0) && (() => {
        const A = stats.automated;
        const money = (v: number | null | undefined, ccy: string) =>
          v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)} ${ccy}`;
        const popLine = (pp: Pop, ccy: string) => (
          <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.7 }}>
            <b style={{ color: "var(--text)" }}>{pp.n}</b> closed
            {pp.n > 0 && <>
              {" · "}<span style={{ color: (pp.total ?? 0) >= 0 ? TONE.ok : TONE.bad,
                                    fontWeight: 600 }}>{money(pp.total, ccy)}</span>
              {" · "}{pp.wins}W/{pp.losses}L
              {" · best "}{money(pp.best, ccy)}{" · worst "}{money(pp.worst, ccy)}
            </>}
            {pp.winRateWithheld && (
              <div style={{ fontSize: 11, color: TONE.warn }}>
                win rate withheld — {pp.winRateWithheld}
              </div>
            )}
          </div>
        );
        return (
          <div style={{ border: "1px solid var(--border)", borderRadius: 10,
                        padding: 14, margin: "14px 0" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
              <span style={{ fontWeight: 600 }}>Desk statistics</span>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                last {stats.windowDays} days · {A.sessions} session(s) traded
              </span>
            </div>

            {stats.caveats.map((c, i) => (
              <div key={i} style={{ fontSize: 12, color: TONE.warn, lineHeight: 1.5,
                                    border: `1px solid ${TONE.warn}`, borderRadius: 6,
                                    padding: "7px 10px", marginBottom: 7 }}>
                {c}
              </div>
            ))}

            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 10 }}>
              <div style={{ flex: 1, minWidth: 260 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
                  Gate said trade — the strategy as designed
                </div>
                {popLine(A.gated, A.currency)}
              </div>
              <div style={{ flex: 1, minWidth: 260 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
                  Gate REFUSED — traded anyway (shadow)
                </div>
                {popLine(A.shadow, A.currency)}
              </div>
            </div>

            {A.byTrigger.length > 0 && (
              <div style={{ marginTop: 12, fontSize: 12 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>
                  How they ended — the exit that actually fired
                </div>
                {A.byTrigger.map((t) => (
                  <span key={t.trigger} style={{ marginRight: 14 }}>
                    {t.trigger} <b>{t.n}</b>{" "}
                    <span style={{ color: t.total >= 0 ? TONE.ok : TONE.bad }}>
                      {money(t.total, A.currency)}
                    </span>
                  </span>
                ))}
              </div>
            )}

            {stats.manual.byCurrency.map((m) => (
              <div key={m.currency} style={{ marginTop: 14, paddingTop: 12,
                                             borderTop: "1px solid var(--border)" }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 3 }}>
                  Manual book — REAL money in {m.currency}, never added to the paper desk above
                </div>
                {popLine(m.stats, m.currency)}
                <div style={{ display: "flex", gap: 18, marginTop: 6, flexWrap: "wrap" }}>
                  <div style={{ minWidth: 220 }}>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>followed our signal</div>
                    {popLine(m.followedSignal, m.currency)}
                  </div>
                  <div style={{ minWidth: 220 }}>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>did NOT follow it</div>
                    {popLine(m.ignoredSignal, m.currency)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        );
      })()}

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
              {/* WHEN IT WENT ON. The broker gives no open time for a position,
                  so this is the decision row that placed it, matched on the OCC
                  strike. A leg we cannot attribute shows "—" and says why on
                  hover rather than borrowing a neighbour's timestamp. */}
              <th style={{ padding: "5px 6px" }}>Placed</th>
              <th style={{ padding: "5px 6px", textAlign: "right" }}>Held</th>
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
                // Timings come from the P&L endpoint, which does the OCC match
                // server-side. Matched on conid: the contract STRING is
                // formatted for humans and is not an identifier.
                const t = pnl0?.open.detail.find((x) => x.conid === l.conid);
                return (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "6px" }}>
                      {l.instrumentName || l.ticker}
                      {l.quantity < 0 && (
                        <span style={{ fontSize: 9, marginLeft: 5, color: "var(--text-muted)" }}>SHORT</span>
                      )}
                    </td>
                    <td style={{ padding: "6px", color: t ? "inherit" : "var(--text-muted)" }}
                        title={t?.whyNoTime ?? ""}>
                      {hhmmss(t?.placedAtUtc)}
                    </td>
                    <td style={{ padding: "6px", textAlign: "right",
                                 color: t?.heldMinutes == null ? "var(--text-muted)" : "inherit" }}>
                      {held(t?.heldMinutes)}
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
        // An OPEN book with nothing closed yet still has a live P&L to show.
        if (!done.length && !openNow && !legs.length) return null;
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
        // LIVE, NOT "WHEN IT CLOSES". Owner, 8 Sep 2026: "we shd be able to
        // see live pnl at any point of time." Both halves already existed and
        // nothing added them up — this cell used to read "P&L lands when the
        // position closes" while the broker's live mark sat one panel above.
        //
        // openPnl is null when there is nothing open, NOT 0: an empty book and
        // a book marked flat are different facts and must not render alike.
        const openPnl = legs.length
          ? legs.reduce((a, l) => a + (l.unrealisedAbs || 0), 0)
          : null;
        // A leg the broker did not mark cannot be added. Counted so the total
        // can say it is understating the book rather than quietly doing it.
        const unmarked = legs.filter((l) => l.unrealisedAbs == null).length;
        const deskTotal = openPnl == null ? sumOf(done) : sumOf(done) + openPnl;
        return (
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "16px 0 8px" }}>
            {cell("Gate said trade — closed", gated.length, gated.length ? sumOf(gated) : null,
                  "the strategy as designed")}
            {cell("Gate REFUSED — traded anyway", over.length, over.length ? sumOf(over) : null,
                  "captured on purpose. A LOSS here is evidence the gate is set right")}
            {cell("Still open — live", legs.length, openPnl,
                  unmarked
                    ? `${unmarked} leg(s) have no broker mark and are NOT in this figure`
                    : "marked by the broker, refreshes every 60s")}
            {cell("Desk total", done.length + legs.length, deskTotal,
                  openPnl == null
                    ? "closed trades only — nothing is open"
                    : "closed trades plus the live mark on what is still open")}
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
                    ? (
                      // WHY, not just THAT. A refusal with no reason is the
                      // same dead end the Lambda log was.
                      <span style={{ color: TONE.off }}>
                        not placed
                        {r.place_error && (
                          <b style={{ color: TONE.warn, fontWeight: 500 }}
                             title={r.place_error}>
                            {" — "}{r.place_error.length > 58
                              ? r.place_error.slice(0, 58) + "…"
                              : r.place_error}
                          </b>
                        )}
                      </span>
                    )
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
        <b style={{ color: "var(--text)" }}>not placed</b> carries the reason where we have it —
        the broker&rsquo;s own words for a rejection, ours for a refusal. Hover for the full text.
        A refusal with no reason is the same dead end as no message at all.
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
