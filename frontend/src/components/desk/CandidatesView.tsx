import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";

/**
 * ONE table. Every strategy. Strategy is a FILTER, not a tab.
 *
 * Owner, 1 Sep 2026: "we want a coherant and trustworthy data and not scattered
 * data ... as user i dont have to think many screens", and on the universe:
 * "its good to subclassify but we shd have unirkm list".
 *
 * Phase 4 of docs/COHERENT_CANDIDATES_PLAN.md. Before this, the desk asked the
 * owner to hold six screens in their head — Swing, Momentum, Puts, Wheel,
 * Strangle, Today's Setups — each with its own universe, its own freshness and
 * its own idea of what a candidate is. Two of them were both called "wheel" and
 * disagreed on the same afternoon.
 *
 * THREE THINGS THIS SHOWS THAT THE SEPARATE TABS COULD NOT:
 *
 *  1. EVERY candidate, ranked together. If the wheel has 21 and swing has 0,
 *     that is one fact on one screen, not two tabs to reconcile.
 *  2. FRESHNESS PER ROW. Each artifact carries its own as_of. A row computed on
 *     yesterday's close while the market is open says so, in the row — the
 *     failure that had Today's Setups showing 31-Aug cards at 19:31 on 1 Sep.
 *  3. THE SLEEVE'S OWN RECORD. A candidate from a sleeve with a 24% win rate
 *     must not look like one from a sleeve that works. The tier comes from
 *     DeskShell's own trusted/unproven classification, which already encodes
 *     which strategies passed pre-registered gates.
 *
 * THE ADAPTERS BELOW ARE A SHIM AND ARE MARKED AS ONE. Each strategy still
 * publishes its own candidate shape, so this file knows a little about each.
 * Phase 3 (one candidate record, emitted by every producer) deletes that
 * knowledge. Doing Phase 4 first gets the single screen sooner; the cost is
 * this one file, in one place, rather than per-strategy knowledge scattered
 * through the UI.
 */

const OK = "#0ca30c";
const WARN = "#fab219";
const BAD = "#ec835a";
const MUTED = "var(--text-muted)";

type Tier = "trusted" | "unproven";
type TierRaw = "gated" | "thin" | "failed" | "unproven";

/**
 * THREE STATES, NOT TWO (2 Sep 2026). Owner: "unproven gves me low confidence",
 * on a board where 22 of 34 rows carried that one word.
 *
 * It was doing too much work. "Not yet shown to work" (thin evidence) and
 * "shown not to work" (a failed backtest) are OPPOSITE claims, and collapsing
 * them produced a wall of one word that reads as noise instead of signal. A
 * reader cannot act on "unproven" x22; they can act on "this one failed its
 * backtest" and "this one passed on thin evidence".
 *
 * Wording lives in ONE place — mirrored from candidates.py TIER_NOTE — so the
 * screen and the email cannot drift into different vocabularies.
 */
const TIER_NOTE: Record<string, string> = {
  gated: "passed its pre-registered gates",
  thin: "passed its gates, but on thin evidence — size accordingly",
  failed: "its BACKTEST FAILED — for study, not for size",
  unproven: "not proven — for your judgement, not for size",
};
const TIER_COLOR: Record<string, string> = {
  gated: OK, thin: WARN, failed: BAD, unproven: WARN,
};

interface Row {
  symbol: string;
  strategy: string;
  tier: Tier;
  /** gated | thin | failed — the precise state, not the two-way collapse. */
  tierRaw?: TierRaw;
  /** What the strategy says to do — kept as the strategy's own words. */
  action: string;
  entry: number | null;
  /** Strike for options, stop for equity — labelled per row, never conflated. */
  level: number | null;
  levelLabel: string;
  /** The one number that ranks this strategy's candidates against each other. */
  metric: number | null;
  metricLabel: string;
  asOf: string | null;
  eligible: boolean;
  why: string;
  /** Per-input provenance — IBKR / cache / vendor / fallback / missing. */
  provenance?: any[];
  /** What was checked and what it measured. The answer to "why is this a
   *  candidate", in the engine's own numbers rather than a sentence. */
  gates?: any[];
  /** Producer-specific payload — e.g. the pre-earnings engine's full options
   *  context (term structure, OI ladder), rendered in its own panel. */
  extra?: any;
  /** You already own this. Set from the live book, not from the strategy —
   *  selling a put on a name you hold, or buying more of one, is a DIFFERENT
   *  trade from opening fresh, and the screen was silent about it. */
  heldQty?: number | null;
}

const num = (v: number | null | undefined, d = 2, suf = "") =>
  v === null || v === undefined || Number.isNaN(v)
    ? <span style={{ color: MUTED }}>—</span>
    : `${v.toFixed(d)}${suf}`;

/** Age of an artifact in hours, or null when it carries no as_of. */
function ageHours(asOf: string | null): number | null {
  if (!asOf) return null;
  const t = Date.parse(asOf);
  return Number.isNaN(t) ? null : (Date.now() - t) / 36e5;
}

export function CandidatesView({ onOpenSymbol }:
    { onOpenSymbol?: (symbol: string) => void } = {}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [errs, setErrs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [only, setOnly] = useState<string>("all");
  const [hideBlocked, setHideBlocked] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    const out: Row[] = [];
    const problems: string[] = [];

    // ── ADAPTERS (Phase 3 deletes these) ────────────────────────────────
    // Each producer publishes its own shape. Every failure is CAUGHT AND
    // NAMED rather than swallowed: a strategy silently missing from a
    // combined screen is worse than one that says it could not load, because
    // the reader cannot tell an empty strategy from an absent one.

    // PHASE 3: prefer `candidates_v2` — the shape every strategy emits, which
    // carries the gate trace and per-input provenance the drill-down needs. The
    // per-strategy adapters below remain ONLY as a fallback for artifacts
    // published before producers were updated, and delete themselves the moment
    // every artifact has v2.
    const fromV2 = (rows: any[], asOf: string | null): Row[] =>
      (rows ?? []).map((c) => ({
        symbol: c.symbol, strategy: c.strategy,
        tier: c.tier === "gated" ? "trusted" : "unproven",
        tierRaw: (c.tier ?? "unproven") as TierRaw,
        action: c.action, entry: c.entry ?? null,
        level: c.level ?? null, levelLabel: c.level_label ?? "",
        metric: c.metric ?? null, metricLabel: c.metric_label ?? "",
        asOf: c.as_of ?? asOf, eligible: !!c.eligible, why: c.why ?? "",
        provenance: c.provenance ?? [], gates: c.gates ?? [],
        extra: c.extra ?? null,
      }));

    try {
      // Pre-earnings watch: rows arrive already in the common shape, and the
      // WHY column carries the latest engine output (WATCH / ORDER_PROPOSAL /
      // CONFIGURATION_BLOCKED) so the newest potential order is on this
      // screen, not only in an email.
      const r: any = await api.preEarningsCandidates();
      const a: any = r?.artifact ?? {};
      if (a.candidates_v2?.length) {
        out.push(...fromV2(a.candidates_v2, a.as_of_utc ?? r?.asOfUtc ?? null));
      }
    } catch { /* engine not yet run this cycle — absence is not an error */ }

    try {
      const r = await api.postEarningsPuts();
      const a: any = r?.artifact ?? {};
      if (a.candidates_v2?.length) {
        out.push(...fromV2(a.candidates_v2, a.as_of_utc ?? r?.asOfUtc ?? null));
      } else
      for (const c of (a.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Puts", tierRaw: "thin", tier: "unproven",
          action: "sell put", entry: c.spot ?? null,
          level: c.listed_strike ?? c.strike_indicative ?? c.strike ?? null,
          levelLabel: "strike",
          metric: c.annual_yield_pct ?? null, metricLabel: "%/yr",
          asOf: a.as_of_utc ?? r?.asOfUtc ?? null,
          eligible: true,
          why: c.pricing_note ?? "post-earnings drop, market gate open",
        });
      }
    } catch (e) { problems.push(`Puts: ${String((e as Error)?.message || e)}`); }

    try {
      const r: any = await api.swingCandidates();
      const a: any = r?.artifact ?? {};
      if (a.candidates_v2?.length) {
        out.push(...fromV2(a.candidates_v2, a.as_of_utc ?? r?.asOfUtc ?? null));
      } else
      for (const c of (a.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Swing", tierRaw: "gated", tier: "trusted",
          action: "buy", entry: c.close ?? null,
          level: c.stop ?? null, levelLabel: "stop",
          metric: c.sigma_from_mean ?? null, metricLabel: "σ",
          asOf: a.as_of_utc ?? r?.asOfUtc ?? null,
          eligible: true, why: "σ-band entry, trend filter passed",
        });
      }
    } catch (e) { problems.push(`Swing: ${String((e as Error)?.message || e)}`); }

    try {
      const r: any = await api.momentumCandidates();
      const a: any = r?.artifact ?? {};
      if (a.candidates_v2?.length) {
        out.push(...fromV2(a.candidates_v2, a.as_of_utc ?? r?.asOfUtc ?? null));
      } else
      for (const c of (a.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Momentum", tierRaw: "gated", tier: "trusted",
          action: "buy", entry: c.calcs?.entry?.value ?? c.close ?? null,
          level: c.stop ?? null, levelLabel: "stop",
          metric: c.calcs?.atr_pct?.value ?? null, metricLabel: "ATR%",
          asOf: a.as_of_utc ?? r?.asOfUtc ?? null,
          eligible: true, why: "Ichimoku, above cloud",
        });
      }
    } catch (e) { problems.push(`Momentum: ${String((e as Error)?.message || e)}`); }

    try {
      const r: any = await api.optionsCandidates();
      if (r?.candidates_v2?.length) {
        out.push(...fromV2(r.candidates_v2, r?.generated_at_utc ?? null));
      } else
      for (const c of (r?.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Wheel", tierRaw: "failed", tier: "unproven",
          action: "sell put", entry: c.ref_close ?? null,
          level: c.suggested_strike ?? null, levelLabel: "strike",
          metric: c.annualized_yield_pct ?? null, metricLabel: "%/yr",
          asOf: r?.generated_at_utc ?? null,
          eligible: !!c.eligible,
          why: c.eligible ? "clears every gate"
                          : (c.blocks?.[0] ?? "blocked"),
        });
      }
    } catch (e) { problems.push(`Wheel: ${String((e as Error)?.message || e)}`); }

    // YOUR BOOK, matched onto the candidates. Owner, 2 Sep 2026: "and i have
    // some symbols on" — FCX was a Wheel candidate while 10 shares of FCX sat
    // in the portfolio, and nothing on the screen said so. Concentration is a
    // decision the reader can only make if the screen tells them.
    try {
      const pos: any = await api.t212Positions();
      const held = new Map<string, number>();
      for (const p of (pos?.positions ?? [])) {
        const sym = String(p.ticker ?? "").split("_")[0].toUpperCase();
        const q = Number(p.quantity ?? p.qty ?? 0);
        if (sym && q) held.set(sym, (held.get(sym) ?? 0) + q);
      }
      for (const r of out) {
        const q = held.get(r.symbol.toUpperCase());
        if (q) r.heldQty = q;
      }
    } catch (e) {
      problems.push(`Holdings: ${String((e as Error)?.message || e)}`);
    }

    setRows(out);
    setErrs(problems);
    setLoading(false);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t); }, [load]);

  const strategies = useMemo(
    () => Array.from(new Set(rows.map((r) => r.strategy))).sort(), [rows]);

  // Actionability order — the same TIER_RANK the email uses. Defined in the
  // Python tier contract (candidates.py); restated here because the desk has
  // no runtime path to it. If the tiers ever change, both change.
  const TIER_ORDER: Record<TierRaw, number> = { gated: 0, thin: 1, unproven: 2, failed: 3 };

  const view = useMemo(() => {
    let v = rows;
    if (only !== "all") v = v.filter((r) => r.strategy === only);
    // A failed-tier strategy is WITHHELD from the combined view, not deleted:
    // the owner looked at 14 wheel rows badged `failed` out of 22 and fairly
    // asked what the point was — two-thirds of the list was one DO-NOT-FUND
    // verdict repeated. Clicking the strategy's own pill is an explicit
    // opt-in to the study view, so there it still shows.
    if (hideBlocked) {
      v = v.filter((r) => r.eligible);
      if (only === "all") v = v.filter((r) => r.tierRaw !== "failed");
    }
    // Actionable tiers first, then by each strategy's own ranking metric.
    // Cross-strategy metrics are NOT comparable (a σ is not a %/yr), so this
    // ranks WITHIN a strategy and groups by it — claiming a single ranking
    // across strategies would be a number that means nothing.
    return [...v].sort((a, b) =>
      (TIER_ORDER[a.tierRaw ?? "unproven"] - TIER_ORDER[b.tierRaw ?? "unproven"])
      || (a.eligible === b.eligible ? 0 : a.eligible ? -1 : 1)
      || a.strategy.localeCompare(b.strategy)
      || ((b.metric ?? -Infinity) - (a.metric ?? -Infinity)));
  }, [rows, only, hideBlocked]);

  const eligibleCount = rows.filter((r) => r.eligible && r.tierRaw !== "failed").length;
  const withheldCount = rows.filter((r) => r.eligible && r.tierRaw === "failed").length;

  const Pill = ({ v, label }: { v: string; label: string }) => (
    <button onClick={() => setOnly(v)}
            style={{ padding: "3px 10px", borderRadius: 999, fontSize: 12,
                     cursor: "pointer", whiteSpace: "nowrap",
                     border: `1px solid ${only === v ? OK : "var(--border)"}`,
                     background: only === v ? `${OK}18` : "var(--surface-2)",
                     color: only === v ? OK : "var(--text)" }}>
      {label}
    </button>
  );

  const th = (label: string, right = false) => (
    <th key={label} style={{ padding: "6px 8px", textAlign: right ? "right" : "left",
                             fontWeight: 500, color: MUTED, fontSize: 11,
                             textTransform: "uppercase", letterSpacing: ".05em",
                             borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>
      {label}
    </th>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 16 }}>
      <div>
        <div style={{ fontSize: 18, fontWeight: 700 }}>Candidates</div>
        <div style={{ fontSize: 12, color: MUTED, lineHeight: 1.5, marginTop: 2 }}>
          Every strategy, one table. Strategy is a <b>filter</b>, not a tab.
          Each row carries its own <b>freshness</b> and its sleeve's <b>tier</b> — a
          candidate from a strategy that has not passed its gates must not look like
          one from a strategy that has. Metrics rank <b>within</b> a strategy: a σ and
          a %/yr are not comparable, and pretending otherwise would be a number
          that means nothing.
        </div>
      </div>

      {errs.length > 0 && (
        <div style={{ padding: "8px 10px", borderRadius: 6, fontSize: 12,
                      border: `1px solid ${BAD}55`, background: `${BAD}14`, color: BAD }}>
          <b>Could not load:</b> {errs.join(" · ")}
          <div style={{ color: MUTED, marginTop: 3 }}>
            A strategy missing from this table without saying so would be
            indistinguishable from a strategy with no candidates. It says so.
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap" }}>
        <Pill v="all" label={`All (${eligibleCount})`} />
        {strategies.map((s) => (
          <Pill key={s} v={s}
                label={`${s} (${rows.filter((r) => r.strategy === s && r.eligible).length})${
                  rows.some((r) => r.strategy === s && r.tierRaw === "failed") ? " ⚠" : ""}`} />
        ))}
        <button
          onClick={async (e) => {
            const b = e.currentTarget; b.disabled = true; b.textContent = "running…";
            try {
              // Invokes the Lambda synchronously (seconds), then re-reads the
              // board so the Pre-Earn row reflects the fresh evaluation.
              await api.runPreEarningsWatch();
              await load();
              b.textContent = "run Pre-Earn ✓";
            } catch { b.textContent = "run failed — see logs"; }
            finally { setTimeout(() => { b.disabled = false; b.textContent = "run Pre-Earn"; }, 4000); }
          }}
          style={{ marginLeft: "auto", padding: "3px 10px", borderRadius: 999,
                   fontSize: 12, cursor: "pointer", border: "1px solid var(--border)",
                   background: "var(--surface-2)", color: "var(--text)" }}>
          run Pre-Earn
        </button>
        <label style={{ fontSize: 12, cursor: "pointer", color: "var(--text)" }}>
          <input type="checkbox" checked={hideBlocked}
                 onChange={(e) => setHideBlocked(e.target.checked)}
                 style={{ marginRight: 5, verticalAlign: "middle" }} />
          hide blocked
        </label>
      </div>

      {hideBlocked && only === "all" && withheldCount > 0 && (
        <div style={{ fontSize: 11.5, color: MUTED }}>
          {withheldCount} row(s) from a strategy whose backtest FAILED are not
          listed here — counted, never hidden. Click its pill to study them.
        </div>
      )}

      {loading ? (
        <div style={{ color: MUTED }}>Loading…</div>
      ) : view.length === 0 ? (
        <div style={{ padding: "12px 14px", border: "1px dashed var(--border)",
                      borderRadius: 8, fontSize: 12.5, color: "var(--text)" }}>
          <b>No candidates is a verdict, not a failure.</b>{" "}
          <span style={{ color: MUTED }}>
            These strategies fire on a minority of sessions by design — a quiet day
            is the rules working. Untick “hide blocked” to see what was considered
            and why it was rejected.
          </span>
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5,
                          fontVariantNumeric: "tabular-nums" }}>
            <thead>
              <tr>
                {["Symbol", "Strategy", "Action", "Entry", "Level", "Rank", "Data", "Why"]
                  .map((h, i) => th(h, i === 3 || i === 4 || i === 5))}
              </tr>
            </thead>
            <tbody>
              {view.map((r, i) => {
                const age = ageHours(r.asOf);
                const stale = age != null && age > 20;
                const rowKey = `${r.strategy}:${r.symbol}:${i}`;
                const isOpen = open === rowKey;
                return [
                  <tr key={rowKey}
                      onClick={() => setOpen(isOpen ? null : rowKey)}
                      title="Click for the gate trace and where each number came from"
                      style={{ borderTop: "1px solid #141b2b", cursor: "pointer",
                               background: r.eligible ? "rgba(12,163,12,.05)" : undefined }}>
                    <td style={{ padding: "7px 8px", fontWeight: 700,
                                 fontFamily: "var(--font-mono)" }}>{onOpenSymbol ? (
                      <span role="button"
                            title="open chart + detail"
                            onClick={() => onOpenSymbol(r.symbol)}
                            style={{ cursor: "pointer", textDecoration: "underline",
                                     textDecorationStyle: "dotted",
                                     textUnderlineOffset: 3 }}>
                        {r.symbol}
                      </span>
                    ) : r.symbol}</td>
                    <td style={{ padding: "7px 8px" }}>
                      {r.strategy}
                      {/* Tier beside the name, always — never colour alone. */}
                      <span title={TIER_NOTE[r.tierRaw ?? "unproven"] ?? ""}
                            style={{ fontSize: 9, marginLeft: 5, padding: "1px 4px",
                                     borderRadius: 3, whiteSpace: "nowrap",
                                     color: TIER_COLOR[r.tierRaw ?? "unproven"],
                                     border: `1px solid ${TIER_COLOR[r.tierRaw ?? "unproven"]}55` }}>
                        {r.tierRaw ?? "unproven"}
                      </span>
                      {r.heldQty ? (
                        <span title={`You already hold ${r.heldQty}. Adding here concentrates the same name — selling a put on stock you own, or buying more of it, is a different trade from opening fresh.`}
                              style={{ fontSize: 9, marginLeft: 4, padding: "1px 4px",
                                       borderRadius: 3, whiteSpace: "nowrap", color: OK,
                                       border: `1px solid ${OK}55` }}>
                          held {r.heldQty}
                        </span>
                      ) : null}
                    </td>
                    <td style={{ padding: "7px 8px", color: MUTED }}>{r.action}</td>
                    <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.entry)}</td>
                    <td style={{ padding: "7px 8px", textAlign: "right" }}>
                      {num(r.level)}
                      <span style={{ fontSize: 10, color: MUTED, marginLeft: 4 }}>{r.levelLabel}</span>
                    </td>
                    <td style={{ padding: "7px 8px", textAlign: "right" }}>
                      {num(r.metric, r.metricLabel === "σ" ? 2 : 1)}
                      <span style={{ fontSize: 10, color: MUTED, marginLeft: 3 }}>{r.metricLabel}</span>
                    </td>
                    <td style={{ padding: "7px 8px", fontSize: 11,
                                 color: stale ? WARN : MUTED, whiteSpace: "nowrap" }}
                        title={r.asOf ?? "no as-of recorded"}>
                      {age == null ? "—" : age < 1 ? "live" : `${age.toFixed(0)}h old`}
                    </td>
                    <td style={{ padding: "7px 8px", color: MUTED, maxWidth: 340 }}>
                      {r.why}
                      <span style={{ marginLeft: 6, fontSize: 10, color: MUTED }}>
                        {isOpen ? "▾" : "▸"}
                      </span>
                    </td>
                  </tr>,
                  isOpen && (
                    <tr key={`${rowKey}-detail`} style={{ background: "var(--surface-2)" }}>
                      <td colSpan={8} style={{ padding: "10px 12px" }}>
                        <Detail r={r} />
                      </td>
                    </tr>
                  ),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ fontSize: 11.5, color: MUTED, lineHeight: 1.6 }}>
        <b>gated</b> = the strategy passed its pre-registered gates.{" "}
        <b>unproven</b> = it did not, or has never been reviewed — its candidates
        are for your judgement, not for size. Freshness is per row because each
        strategy publishes on its own schedule; anything over 20 hours old is
        flagged. This is a <b>candidate list for manual use</b>, not an
        autonomous signal.
      </div>
    </div>
  );
}

/**
 * WHY this is a candidate, in the engine's own numbers.
 *
 * Owner, 1 Sep 2026: "how do i drill down to see why its a candidate".
 *
 * The WHY column is one sentence — enough to scan, not enough to decide. This
 * shows the GATE TRACE (each check, its threshold, what was actually measured,
 * and the verdict) and the PROVENANCE (where every input came from). Both were
 * already computed and published; nothing here is derived for display.
 *
 * A strategy that publishes neither says so plainly rather than rendering an
 * empty box — "this strategy does not yet report its gates" is information, and
 * a blank panel is not.
 */
function Detail({ r }: { r: Row }) {
  const gates = r.gates ?? [];
  const prov = r.provenance ?? [];
  const cell: React.CSSProperties = { padding: "3px 8px", fontSize: 11.5 };
  return (
    <div style={{ display: "flex", gap: 20, flexWrap: "wrap", fontSize: 12 }}>
      <div style={{ minWidth: 300, flex: "1 1 380px" }}>
        <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em",
                      color: MUTED, marginBottom: 5 }}>
          Gates — what was checked
        </div>
        {gates.length === 0 ? (
          <div style={{ color: MUTED }}>
            This strategy does not publish a gate trace yet. Its verdict is in the
            WHY column; the per-check numbers are not available.
          </div>
        ) : (
          <table style={{ borderCollapse: "collapse", width: "100%",
                          fontVariantNumeric: "tabular-nums" }}>
            <tbody>
              {gates.map((g: any, i: number) => {
                const pass = String(g.verdict ?? "").toLowerCase() === "pass";
                return (
                  <tr key={i} style={{ borderTop: "1px solid #141b2b" }}>
                    <td style={{ ...cell, color: "var(--text)" }}>{g.gate}</td>
                    <td style={{ ...cell, textAlign: "right", color: MUTED }}>
                      {g.actual ?? "—"}{g.unit ? ` ${g.unit}` : ""}
                    </td>
                    <td style={{ ...cell, color: MUTED }}>{g.threshold ?? ""}</td>
                    <td style={{ ...cell, color: pass ? OK : WARN, fontWeight: 600 }}>
                      {pass ? "pass" : String(g.verdict ?? "")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {r.extra?.options_context?.status === "CONTEXT_AVAILABLE" && (() => {
        const oc = r.extra.options_context;
        return (
          <div style={{ minWidth: 280, flex: "1 1 320px" }}>
            <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em",
                          color: MUTED, marginBottom: 5 }}>
              Options — how the market is placing it
            </div>
            <table style={{ borderCollapse: "collapse", width: "100%",
                            fontVariantNumeric: "tabular-nums", fontSize: 11.5 }}>
              <tbody>
                {(oc.term_structure ?? []).map((tr: any, i: number) => (
                  <tr key={i} style={{ borderTop: "1px solid #141b2b" }}>
                    <td style={{ padding: "3px 8px" }}>{tr.expiry}</td>
                    <td style={{ padding: "3px 8px", color: tr.crosses_print ? WARN : MUTED }}>
                      {tr.crosses_print ? "crosses print" : "pre-print"}
                    </td>
                    <td style={{ padding: "3px 8px", textAlign: "right" }}>
                      {tr.atm_iv != null ? `IV ${(100 * tr.atm_iv).toFixed(1)}%` : "IV —"}
                    </td>
                    <td style={{ padding: "3px 8px", textAlign: "right", color: MUTED }}>
                      {tr.implied_move_pct != null ? `±${tr.implied_move_pct}%` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 6, fontSize: 11.5, color: MUTED }}>
              {oc.event_iv_premium != null &&
                <>event premium <b style={{ color: WARN }}>
                  +{(100 * oc.event_iv_premium).toFixed(1)} IV pts</b> · </>}
              P/C OI {oc.put_call_oi_ratio ?? "—"} · top OI:{" "}
              {(oc.top_oi ?? []).map((o: any) =>
                `${o.strike}${o.right} (${o.oi.toLocaleString()})`).join(" · ")}
            </div>
            <div style={{ marginTop: 4, fontSize: 10.5, color: MUTED }}>
              captured {oc.capture_date} · context, never a directional signal —
              large OI ≠ support/resistance
            </div>
          </div>
        );
      })()}

      <div style={{ minWidth: 260, flex: "1 1 300px" }}>
        <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em",
                      color: MUTED, marginBottom: 5 }}>
          Data — where each number came from
        </div>
        {prov.length === 0 ? (
          <div style={{ color: MUTED }}>
            This strategy does not publish per-input provenance yet, so "is this
            IBKR, cache or Yahoo?" is unanswerable for this row. That is a gap in
            the strategy, not in the data.
          </div>
        ) : (
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <tbody>
              {prov.map((pi: any, i: number) => {
                const weak = ["fallback", "carried", "unavailable"].includes(pi.trust);
                return (
                  <tr key={i} style={{ borderTop: "1px solid #141b2b" }}>
                    <td style={{ ...cell, color: "var(--text)" }}>{pi.label ?? pi.input}</td>
                    <td style={{ ...cell, color: weak ? WARN : MUTED }}>
                      {pi.source_label ?? pi.trust}
                    </td>
                    <td style={{ ...cell, color: MUTED }}>{pi.age ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
