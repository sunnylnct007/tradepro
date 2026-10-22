/**
 * ActionBoard — "what am I supposed to do today?", and nothing else.
 *
 * Owner, 24 Sep 2026: *"when i got to TradePro UI i do not want to see noise.
 * I want to see what I am supposed to do. Buy, sell, etc."*
 *
 * THE PROBLEM THIS REPLACES. The desk landed on seven cards — KPIs, strategy
 * health, P&L truth, signal audit, broker book, equity tracking, fill replay —
 * before the first card that says to do anything. The boards underneath then
 * showed 82 option rows to surface 18, and 26 swing rows to surface 25. Every
 * one of those numbers is true and none of them is an instruction.
 *
 * So this card carries ONLY rows that name an action, states the action as a
 * verb first, and puts the reason on the same line. Everything else keeps
 * living in the boards below; nothing here replaces them.
 *
 * THREE RULES IT WILL NOT BREAK.
 *
 * 1. NOTHING IS SILENTLY WITHHELD. A row dropped for being blocked, unproven
 *    or failed is COUNTED in the footer with the reason. "No actions today" is
 *    a legitimate and frequent answer — the swing rule fires on ~31% of
 *    sessions — but a blank card that cannot distinguish "nothing qualifies"
 *    from "nothing loaded" is the false-clear this desk has been bitten by.
 *
 * 2. OPTIONS ARE RANKED BY EXPECTANCY, NOT BY YIELD OR BY DELTA. Ranking puts
 *    by keep-probability put ORCL top of the board on 24 Sep at 80.1% — and
 *    ORCL was the only NEGATIVE-expectancy row on it, because when it breaches
 *    it goes a median 10% past the strike. Rare-but-deep beats frequent-but-
 *    shallow and delta cannot see it. The arithmetic is the put-overlay
 *    study's own:  EV = premium collected − P(assigned) × depth when assigned.
 *
 * 3. THE EVIDENCE TIER TRAVELS WITH THE ACTION. A gated sleeve and a screen
 *    whose strategy failed its backtest may both say "sell a put"; they are not
 *    the same claim and must not read as one.
 */
import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";

const TONE = {
  ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30",
  dim: "var(--text-muted)",
};

/** The measured median put bid-ask on this desk, as a FRACTION of mid
 *  (THETA_EARLY_CLOSE_GATES_V1). Paid on every round trip, so an expectancy
 *  thinner than the spread is not an edge — it is noise wearing one. */
const SPREAD_FRAC = 0.089;

type Act = {
  key: string;
  /** WHICH SECTION this belongs in. Separate from `verb` on purpose: the two
   *  sections below used to be filtered by matching the verb STRING, so the
   *  moment a put row could legitimately say "SELL A PUT SPREAD" it would have
   *  matched neither filter and vanished from the board entirely — a silent
   *  withholding, which is the one thing this card promises never to do.
   *  `verb` is display text and free to vary; `kind` is what the code reads. */
  kind: "buy" | "put";
  verb: string;            // BUY / SELL A PUT / SELL A PUT SPREAD — display only
  symbol: string;
  detail: string;          // the level, in the units that trade
  why: string;
  /** The structure-degradation reasons, kept as separate strings rather than
   *  pre-joined into `why`. On an ORANGE day EVERY put row carries the same
   *  one, and joining it in made each row wrap to a second full-width line of
   *  identical text — ~250 characters repeated fifteen times, burying the
   *  strikes and the risk that actually differ. Kept apart so the shared ones
   *  can be said ONCE, above the list. */
  notes: string[];
  /** The full sentences behind `notes`, for the tooltip. */
  noteDetail?: string;
  strategy: string;
  gated: boolean;
  /** Expectancy per cycle, % of collateral, net of the measured spread.
   *  Null for equity rows, which this card does not pretend to price. */
  ev: number | null;
  evBasis?: string;
  rank: number;            // sort key, higher first
};

function fmt(n: number | null | undefined, d = 2) {
  return n === null || n === undefined || Number.isNaN(n) ? "—" : n.toFixed(d);
}

/** Whole dollars. Max loss and max gain are decision numbers read at a glance,
 *  and cents on a $4,065 worst case are noise that costs legibility. */
function usd(n: number | null | undefined) {
  return n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : `${n < 0 ? "-" : ""}$${Math.abs(Math.round(n)).toLocaleString()}`;
}

export function ActionBoard() {
  const [acts, setActs] = useState<Act[]>([]);
  const [withheld, setWithheld] = useState<string[]>([]);
  const [errs, setErrs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showWhy, setShowWhy] = useState(false);

  const load = useCallback(async () => {
    const out: Act[] = [];
    const held: string[] = [];
    const problems: string[] = [];

    // ── EQUITY, from the gated sleeves ──────────────────────────────────
    // SLEEVES IN WIND-DOWN DO NOT PROPOSE ENTRIES.
    //
    // All three equity sleeves run --wind-down (exits only) since 20 Oct 2026,
    // after ENTRY_EDGE_GATES_V1 graded their entries NEGATIVE against both
    // controls on 8 of 8 strategy x horizon combinations. The engine filters
    // every BUY at the order chokepoint — so the eight "BUY HAE / BUY GME /
    // BUY AAPL" rows leading this card were instructions the desk had already
    // decided it would never place.
    //
    // A card whose entire purpose is "what am I supposed to do today" must not
    // lead with trades that cannot happen. They are WITHHELD AND COUNTED, not
    // hidden: the footer says how many and why, which is this card's first
    // rule.
    const WINDING_DOWN = new Set(["Swing", "Momentum"]);

    const equity = async (
      fetcher: () => Promise<any>, label: string,
    ) => {
      try {
        const r: any = await fetcher();
        const a: any = r?.artifact ?? {};
        const rows: any[] = a.candidates_v2 ?? [];
        if (!rows.length) { held.push(`${label}: no rows published yet`); return; }
        const gated = rows[0]?.tier === "gated";
        if (WINDING_DOWN.has(label)) {
          const live = rows.filter((c: any) => {
            const act = String(c.action || "").toLowerCase();
            return c.eligible && (act === "buy" || act === "consider");
          }).length;
          held.push(`${label}: ${live} entry signal(s) not shown — the sleeve runs `
                  + `exits-only, so these would not be placed`);
          return;
        }
        let blocked = 0;
        for (const c of rows) {
          const act = String(c.action || "").toLowerCase();
          if (!c.eligible || (act !== "buy" && act !== "consider")) { blocked++; continue; }
          out.push({
            key: `${label}-${c.symbol}`, kind: "buy", verb: "BUY", symbol: c.symbol, notes: [],
            detail: c.entry != null ? `limit ~${fmt(c.entry)}` : "",
            why: String(c.why || "").split("·")[0].trim(),
            strategy: label, gated, ev: null,
            // Equity rows rank by how stretched the dip is — the rule's own
            // entry statistic — NOT by anything this card invents.
            rank: 1000 + (parseFloat(String(c.why).match(/-?\d+\.\d+(?=σ)/)?.[0] ?? "0") * -1),
          });
        }
        if (blocked) held.push(`${label}: ${blocked} row(s) not actionable today`);
      } catch (e: any) {
        problems.push(`${label} could not load — ${e?.message ?? "unknown error"}`);
      }
    };

    await equity(() => api.swingCandidates(), "Swing");
    await equity(() => api.momentumCandidates(), "Momentum");

    // ── OPTIONS, ranked by expectancy net of the spread ─────────────────
    try {
      const r: any = await api.optionsCandidates();
      const rows: any[] = r?.candidates ?? [];
      if (!rows.length) {
        held.push("Options: no rows published yet");
      } else {
        let blocked = 0;
        for (const c of rows) {
          if (!c.eligible) { blocked++; continue; }
          const sc = c.sigma_context ?? {};
          const hc = c.history_check ?? {};
          const dte = c.dte, ann = c.annualized_yield_pct;
          const pHist = hc.breach_pct, depth = hc.median_breach_depth_pct;
          let ev: number | null = null;
          let basis: string | undefined;
          if (ann != null && dte) {
            const prem = (ann * dte) / 365;               // collected this cycle
            if (pHist != null && depth != null) {
              ev = prem - (pHist / 100) * depth - prem * SPREAD_FRAC;
              basis = `${fmt(prem)}% premium − ${fmt(pHist, 1)}% × ${fmt(depth, 1)}% `
                    + `assignment − spread`;
            }
          }
          // THE STRUCTURE IS PART OF THE INSTRUCTION. "Sell a put" and "sell a
          // put spread" are different trades with different worst cases, and a
          // board that renders both as the former is telling the owner to do
          // something other than what the engine decided.
          const econ = c.economics ?? null;
          const isSpread = c.structure === "BULL_PUT_SPREAD";
          // Max loss first. On a card whose job is "what do I do today", the
          // number that decides whether to do it is what you can lose — the
          // credit is the reason you are tempted, not the reason you are safe.
          const risk = econ
            ? (econ.unbounded
                ? `risk ${usd(econ.max_loss_usd)} (NO floor) → ${usd(econ.max_gain_usd)}`
                : `risk ${usd(econ.max_loss_usd)} → ${usd(econ.max_gain_usd)}`)
            : c.economics_note ? "max loss not priced" : null;
          const level = isSpread && econ?.long_strike != null
            ? `${econ.short_strike}/${econ.long_strike} put spread`
            : `${c.suggested_strike} strike`;
          out.push({
            key: `wheel-${c.symbol}`, kind: "put",
            verb: isSpread ? "SELL A PUT SPREAD" : "SELL A PUT",
            symbol: c.symbol,
            detail: [level, `${dte}d`, risk].filter(Boolean).join(" · "),
            // A structure we did NOT pick first is a fact about the trade, and
            // it leads the reason — the owner has to see that this is not the
            // naked put they would have expected, and why.
            // SHORT TAGS, NOT REPEATED PROSE. Each note ran ~130 characters
            // and wrapped every row onto a second full-width line — on this
            // board 32 rows carried the ORANGE sentence and 20 the earnings
            // one, verbatim, burying the strikes and the risk that actually
            // differ between rows. The full sentence survives as the tag's
            // tooltip, so the reasoning is one hover away rather than gone.
            notes: (c.structure_notes ?? []).map((n: string) => {
              if (/not permitted in/.test(n)) {
                const regime = n.match(/not permitted in (\w+)/)?.[1] ?? "regime";
                return `${regime} → spread`;
              }
              if (/[Ee]arnings/.test(n)) return "earnings → floored";
              return n;
            }),
            noteDetail: (c.structure_notes ?? []).join("\n\n"),
            why: sc.assignment_prob_pct != null
              ? `${fmt(100 - sc.assignment_prob_pct, 0)}% chance it expires worthless`
              : "assignment odds unavailable",
            // The wheel's STRATEGY failed its backtest; this screen is being
            // used as a FILTER. Both are true and the badge must not blur them.
            strategy: "Options screen", gated: false, ev, evBasis: basis,
            rank: ev ?? -999,
          });
        }
        if (blocked) held.push(`Options: ${blocked} row(s) blocked by a gate`);
      }
    } catch (e: any) {
      problems.push(`Options could not load — ${e?.message ?? "unknown error"}`);
    }

    out.sort((a, b) => b.rank - a.rank);
    setActs(out); setWithheld(held); setErrs(problems); setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const buys = acts.filter((a) => a.kind === "buy");
  // An expectancy thinner than the spread is not an edge. Shown, but below the
  // line and labelled, never silently dropped.
  const puts = acts.filter((a) => a.kind === "put");
  const putsWorth = puts.filter((p) => (p.ev ?? -1) > 0);
  const putsThin = puts.filter((p) => (p.ev ?? -1) <= 0);

  // SHOW THE BEST ONES, COUNT THE REST.
  //
  // Owner, 22 Oct 2026: "too many symbols are noise". Screening more names and
  // printing all of them are different decisions, and only the second makes a
  // board unreadable: 82 names already produced 34 positive-expectancy rows,
  // and this card's own header has complained since September that the boards
  // "showed 82 option rows to surface 18".
  //
  // Rows are already sorted by expectancy, so the top of the list IS the
  // answer to "what should I do today". The tail is one click away and its
  // size is stated — never silently dropped, which is this card's first rule
  // and the reason this is a toggle rather than a filter.
  const TOP_N = 12;
  const [showAllPuts, setShowAllPuts] = useState(false);
  const putsShown = showAllPuts ? putsWorth : putsWorth.slice(0, TOP_N);
  const putsHidden = putsWorth.length - putsShown.length;

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 10, padding: "14px 16px",
      background: "var(--surface)", marginBottom: 14,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 17, letterSpacing: 0.2 }}>Today — what to do</h2>
        <span style={{ color: TONE.dim, fontSize: 12 }}>
          {loading ? "loading…" : `${buys.length} to buy · ${putsWorth.length} put(s) worth selling`}
        </span>
        <button
          onClick={() => setShowWhy((v) => !v)}
          style={{
            marginLeft: "auto", background: "none", border: "1px solid var(--border)",
            borderRadius: 6, color: TONE.dim, fontSize: 11, padding: "3px 8px", cursor: "pointer",
          }}
        >{showWhy ? "hide" : "what is not here"}</button>
      </div>

      {errs.length > 0 && (
        <div style={{ marginTop: 8, color: TONE.bad, fontSize: 12 }}>
          {errs.map((e) => <div key={e}>⚠ {e}</div>)}
          <div style={{ color: TONE.dim }}>
            This card is INCOMPLETE — a lane that cannot load is not a lane with
            nothing in it.
          </div>
        </div>
      )}

      {!loading && acts.length === 0 && errs.length === 0 && (
        <div style={{ marginTop: 10, fontSize: 13 }}>
          <b>Nothing to do today.</b>
          <div style={{ color: TONE.dim, marginTop: 3 }}>
            Every lane loaded and none produced an action. The swing rule fires
            on about 31% of sessions, so a quiet day is the rule working rather
            than a fault.
          </div>
        </div>
      )}

      {buys.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {buys.map((a) => (
            <Line key={a.key} a={a} />
          ))}
        </div>
      )}

      {putsWorth.length > 0 && (
        <div style={{ marginTop: buys.length ? 14 : 12 }}>
          {putsShown.map((a) => <Line key={a.key} a={a} />)}
          {(putsHidden > 0 || showAllPuts) && (
            <div
              onClick={() => setShowAllPuts((v) => !v)}
              style={{
                fontSize: 11.5, color: TONE.dim, cursor: "pointer",
                padding: "6px 0 2px", userSelect: "none",
              }}
            >
              {showAllPuts
                ? `▲ show only the best ${TOP_N}`
                : `▼ ${putsHidden} more worth selling, ranked below these — `
                  + `show all ${putsWorth.length}`}
            </div>
          )}
        </div>
      )}

      {putsThin.length > 0 && (
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
          <div style={{ color: TONE.dim, fontSize: 11, marginBottom: 4 }}>
            {putsThin.length} put(s) pass every gate but their expectancy does
            not clear the 8.9% bid-ask this desk actually pays. Listed so the
            screen is not quietly editing itself.
          </div>
          {putsThin.map((a) => (
            <div key={a.key} style={{ color: TONE.dim, fontSize: 12, padding: "2px 0" }}>
              {a.symbol} {a.detail} — EV {fmt(a.ev)}%
            </div>
          ))}
        </div>
      )}

      {showWhy && (
        <div style={{
          marginTop: 12, paddingTop: 8, borderTop: "1px solid var(--border)",
          fontSize: 12, color: TONE.dim,
        }}>
          <b style={{ color: "var(--text)" }}>What is not on this card</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {withheld.map((w) => <li key={w}>{w}</li>)}
            <li>
              Rows that say watch, hold or research. They are real and they are
              on the boards below; they are not instructions.
            </li>
          </ul>
          <div style={{ marginTop: 6 }}>
            Puts are ranked by expectancy — premium collected, minus how often
            this name has historically fallen past the strike times how far it
            went when it did, minus the spread. Ranking by yield or by delta puts
            the worst trade on top: on 24 Sep that was ORCL, highest
            keep-probability on the board and the only negative expectancy on it.
          </div>
        </div>
      )}
    </div>
  );
}

function Line({ a }: { a: Act }) {
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap",
      padding: "6px 0", borderBottom: "1px solid var(--border-subtle, transparent)",
    }}>
      <span style={{
        color: a.verb === "BUY" ? TONE.ok : TONE.ok, fontWeight: 700,
        fontSize: 13, minWidth: 92,
      }}>{a.verb}</span>
      <span style={{ fontWeight: 600, fontSize: 14, minWidth: 64 }}>{a.symbol}</span>
      <span style={{ fontSize: 13 }}>{a.detail}</span>
      {a.ev != null && (
        <span style={{ fontSize: 12, color: TONE.ok }} title={a.evBasis}>
          EV +{fmt(a.ev)}%
        </span>
      )}
      {a.notes.map((n) => (
        <span key={n} title={a.noteDetail} style={{
          fontSize: 10.5, color: TONE.warn, border: `1px solid ${TONE.warn}`,
          borderRadius: 4, padding: "0 5px", whiteSpace: "nowrap", opacity: 0.85,
        }}>{n}</span>
      ))}
      <span style={{ fontSize: 12, color: TONE.dim }}>{a.why}</span>
      <span style={{
        marginLeft: "auto", fontSize: 10, letterSpacing: 0.3,
        color: a.gated ? TONE.ok : TONE.warn,
        border: `1px solid ${a.gated ? TONE.ok : TONE.warn}`,
        borderRadius: 4, padding: "1px 5px", whiteSpace: "nowrap",
      }} title={a.gated
        ? "This rule passed its pre-registered gates on out-of-sample trades."
        : "The wheel STRATEGY failed its backtest. This screen is being used as a filter on trades you choose, which is a different claim."}>
        {a.gated ? "GATED" : "FILTER ONLY"}
      </span>
    </div>
  );
}
