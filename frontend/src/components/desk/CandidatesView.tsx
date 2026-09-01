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

interface Row {
  symbol: string;
  strategy: string;
  tier: Tier;
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

export function CandidatesView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [errs, setErrs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [only, setOnly] = useState<string>("all");
  const [hideBlocked, setHideBlocked] = useState(true);

  const load = useCallback(async () => {
    const out: Row[] = [];
    const problems: string[] = [];

    // ── ADAPTERS (Phase 3 deletes these) ────────────────────────────────
    // Each producer publishes its own shape. Every failure is CAUGHT AND
    // NAMED rather than swallowed: a strategy silently missing from a
    // combined screen is worse than one that says it could not load, because
    // the reader cannot tell an empty strategy from an absent one.

    try {
      const r = await api.postEarningsPuts();
      const a: any = r?.artifact ?? {};
      for (const c of (a.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Puts", tier: "unproven",
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
      for (const c of (a.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Swing", tier: "trusted",
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
      for (const c of (a.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Momentum", tier: "trusted",
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
      for (const c of (r?.candidates ?? [])) {
        out.push({
          symbol: c.symbol, strategy: "Wheel", tier: "unproven",
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

    setRows(out);
    setErrs(problems);
    setLoading(false);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 120000); return () => clearInterval(t); }, [load]);

  const strategies = useMemo(
    () => Array.from(new Set(rows.map((r) => r.strategy))).sort(), [rows]);

  const view = useMemo(() => {
    let v = rows;
    if (only !== "all") v = v.filter((r) => r.strategy === only);
    if (hideBlocked) v = v.filter((r) => r.eligible);
    // Eligible first, then by each strategy's own ranking metric. Cross-strategy
    // metrics are NOT comparable (a σ is not a %/yr), so this ranks WITHIN a
    // strategy and groups by it — claiming a single ranking across strategies
    // would be a number that means nothing.
    return [...v].sort((a, b) =>
      (a.eligible === b.eligible ? 0 : a.eligible ? -1 : 1)
      || a.strategy.localeCompare(b.strategy)
      || ((b.metric ?? -Infinity) - (a.metric ?? -Infinity)));
  }, [rows, only, hideBlocked]);

  const eligibleCount = rows.filter((r) => r.eligible).length;

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
                label={`${s} (${rows.filter((r) => r.strategy === s && r.eligible).length})`} />
        ))}
        <label style={{ marginLeft: "auto", fontSize: 12, cursor: "pointer", color: "var(--text)" }}>
          <input type="checkbox" checked={hideBlocked}
                 onChange={(e) => setHideBlocked(e.target.checked)}
                 style={{ marginRight: 5, verticalAlign: "middle" }} />
          hide blocked
        </label>
      </div>

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
                return (
                  <tr key={`${r.strategy}:${r.symbol}:${i}`}
                      style={{ borderTop: "1px solid #141b2b",
                               background: r.eligible ? "rgba(12,163,12,.05)" : undefined }}>
                    <td style={{ padding: "7px 8px", fontWeight: 700,
                                 fontFamily: "var(--font-mono)" }}>{r.symbol}</td>
                    <td style={{ padding: "7px 8px" }}>
                      {r.strategy}
                      {/* Tier beside the name, always — never colour alone. */}
                      <span title={r.tier === "trusted"
                                    ? "passed its pre-registered gates"
                                    : "has NOT passed its gates — candidates are for review, not size"}
                            style={{ fontSize: 9, marginLeft: 5, padding: "1px 4px",
                                     borderRadius: 3, whiteSpace: "nowrap",
                                     color: r.tier === "trusted" ? OK : WARN,
                                     border: `1px solid ${r.tier === "trusted" ? OK : WARN}55` }}>
                        {r.tier === "trusted" ? "gated" : "unproven"}
                      </span>
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
                    <td style={{ padding: "7px 8px", color: MUTED, maxWidth: 340 }}>{r.why}</td>
                  </tr>
                );
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
