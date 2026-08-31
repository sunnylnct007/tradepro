import { useMemo, useState } from "react";

/**
 * The wheel / CSP board as a SCANNABLE table.
 *
 * Owner, 31 Aug 2026: "these things confuse me as investor ... think how we can
 * improve our screen. Barchart does it nicely", and "we need to be able to
 * decide based on screen not get confused".
 *
 * WHAT WAS WRONG. Every row carried a paragraph justifying itself — "IV/HV 0.88
 * < 1.00 — the option prices LESS movement than the stock is delivering, so the
 * quoted delta UNDERSTATES assignment risk. Not a block: size the strike off
 * realised vol..." — across 36 rows, 35 of which were rejections. You cannot
 * scan that, sort it, or compare two names. The board argued with the reader
 * instead of showing them data.
 *
 * WHAT BARCHART GETS RIGHT and this now copies: fixed numeric columns, sortable
 * on every one, one short reason code per row, and the reader decides. The
 * prose still exists — it is good writing and it explains real risk — but it
 * moves behind a click instead of being shouted 36 times at once.
 *
 * COLOUR IS ALMOST ABSENT, and that is deliberate. The regime has four states
 * (GREEN/YELLOW/ORANGE/RED) and the obvious move is to colour them. Running the
 * palette validator killed that: status-warning (#fab219) and status-serious
 * (#ec835a) separate by ΔE 13.6 on the dark surface, below the 15 floor — hard
 * to tell apart even with full colour vision, before considering CVD. So regime
 * is PLAIN TEXT, and colour is spent on the single thing the reader is deciding:
 * is this tradeable or not. Every status also carries a word, never colour
 * alone.
 */

// Status steps, dark-surface (validated: CVD ΔE 11.3, normal 27.6, contrast >=3:1).
const OK = "#0ca30c";       // good — eligible
const WARN = "#fab219";     // warning — degraded inputs
const MUTED = "var(--text-muted)";


// ── PROVENANCE, lifted from OptionsDesk (31 Aug 2026) ───────────────────────
// It rendered the "FALLBACK bars, spot, premium, div" badge on the prose table
// that this file replaced. Losing it would have removed the one thing that
// answers "is this row's number IBKR, cache or Yahoo?" — which the owner asked
// about directly today. The badge belongs with the numbers it qualifies.
export type ProvTrust =
  | "golden" | "derived" | "vendor" | "fallback" | "carried" | "unavailable";
export interface ProvInput {
  input: string;
  label: string;
  trust: ProvTrust;
  source_label: string;
  detail: string;
  age?: string | null;
}
export interface ProvenanceBlock {
  worst: ProvTrust;
  summary: string;
  inputs: ProvInput[];
}

export interface WheelRow {
  symbol: string;
  regime: string | null;
  iv_hv_ratio?: number | null;
  open_interest: number | null;
  spread_usd: number | null;
  eligible: boolean;
  blocks: string[];
  warnings: string[];
  suggested_strike: number | null;
  // WHICH CONTRACT to place. A strike and a premium without an expiry is not a
  // tradeable instruction — several expiries are listed in any given week.
  expiry?: string | null;
  expiry_kind?: string | null;
  dte?: number | null;
  suggested_delta: number | null;
  suggested_premium: number | null;
  premium_source?: string | null;
  premium_age_h?: number | null;
  // NAMES MATCH THE PAYLOAD EXACTLY (31 Aug 2026). They did not, and an
  // `as unknown as WheelRow[]` cast at the call site silenced the compiler:
  //   annual_yield_pct -> annualized_yield_pct
  //   spot             -> ref_close
  //   forward          -> forward_price
  // The Yield column rendered "—" on every row, and because yield is also the
  // DEFAULT SORT KEY, the header "top 5, ranked by annualised yield" was
  // ranking on -1 for all of them. The board claimed an order it was not
  // applying. Guessing an identifier instead of reading it is the same failure
  // as the 7638 comment and the "g3_ibkr" source string.
  annualized_yield_pct?: number | null;
  ref_close?: number | null;
  provenance?: ProvenanceBlock | null;
  forward_price?: number | null;
}

/**
 * Collapse a block sentence to ONE word. Six codes learned once, then the whole
 * board reads in seconds — instead of a paragraph per row.
 */
function reasonCode(blocks: string[]): string | null {
  if (!blocks?.length) return null;
  const t = blocks.join(" ").toLowerCase();
  if (t.includes("regime")) return "regime";
  if (t.includes("open interest") || t.includes("spread")) return "liquidity";
  if (t.includes("52-week")) return "extended";
  if (t.includes("iv/hv") || t.includes("iv-hv")) return "IV/HV";
  if (t.includes("yield")) return "yield";
  if (t.includes("earnings")) return "earnings";
  if (t.includes("carried") || t.includes("not actionable")) return "stale";
  return "blocked";
}

/** One badge for data quality, instead of FALLBACK/CARRIED scattered in prose. */
function quality(r: WheelRow): { label: string; degraded: boolean } {
  const src = r.premium_source ?? null;
  if (src === "live_mid") return { label: "live", degraded: false };
  if (src === "carried_last_live")
    return { label: `carried${r.premium_age_h ? ` ${r.premium_age_h.toFixed(0)}h` : ""}`, degraded: true };
  if (src === "prev_close_indicative") return { label: "prev close", degraded: true };
  return { label: "no quote", degraded: true };
}

type SortKey = "symbol" | "yield" | "oi" | "ivhv" | "delta" | "premium" | "strike" | "dte";

// GENERIC over the row type on purpose. The callers hold a richer `Candidate`
// and their handlers need those extra fields, so a non-generic
// `(r: WheelRow) => void` would force a cast at the call site — and a cast is
// precisely what hid the annual_yield_pct/annualized_yield_pct mismatch that
// blanked this table's Yield column and unranked it.
export function WheelBoardTable<T extends WheelRow>({ rows, onAnalyze, onRecord, busy }: {
  rows: T[];
  // Actions live in the EXPANDED row, not in a trailing column. A button per
  // row on 82 rows is 164 controls competing with the numbers; a button beside
  // the reasoning is a decision made with its context in view.
  onAnalyze?: (r: T) => void;
  onRecord?: (r: T) => void;
  busy?: boolean;
}) {
  const [sort, setSort] = useState<SortKey>("yield");
  const [desc, setDesc] = useState(true);
  const [onlyEligible, setOnlyEligible] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const view = useMemo(() => {
    const pick = (r: T): number | string => {
      switch (sort) {
        case "symbol": return r.symbol;
        case "oi": return r.open_interest ?? -1;
        case "ivhv": return r.iv_hv_ratio ?? -1;
        case "delta": return r.suggested_delta ?? -1;
        case "premium": return r.suggested_premium ?? -1;
        case "dte": return r.dte ?? 9999;
        case "strike": return r.suggested_strike ?? -1;
        default: return r.annualized_yield_pct ?? -1;
      }
    };
    const f = onlyEligible ? rows.filter((r) => r.eligible) : rows;
    return [...f].sort((a, b) => {
      // Eligible ALWAYS floats to the top regardless of sort. The one tradeable
      // name was previously buried among 35 rejections.
      if (a.eligible !== b.eligible) return a.eligible ? -1 : 1;
      const x = pick(a), y = pick(b);
      const c = typeof x === "string" ? String(x).localeCompare(String(y)) : (x as number) - (y as number);
      return desc ? -c : c;
    });
  }, [rows, sort, desc, onlyEligible]);

  const th = (label: string, key?: SortKey, right = false) => (
    <th
      onClick={key ? () => { if (key === sort) setDesc(!desc); else { setSort(key); setDesc(true); } } : undefined}
      style={{
        padding: "6px 8px", textAlign: right ? "right" : "left", fontWeight: 500,
        color: key === sort ? "var(--text)" : MUTED, fontSize: 11,
        textTransform: "uppercase", letterSpacing: ".05em",
        cursor: key ? "pointer" : "default", whiteSpace: "nowrap",
        borderBottom: "1px solid var(--border)",
      }}
    >
      {label}{key === sort ? (desc ? " ▾" : " ▴") : ""}
    </th>
  );

  const num = (v: number | null | undefined, d = 2, suffix = "") =>
    v === null || v === undefined ? <span style={{ color: MUTED }}>—</span>
      : `${v.toFixed(d)}${suffix}`;

  const eligibleCount = rows.filter((r) => r.eligible).length;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0 10px" }}>
        <span style={{ fontSize: 12, color: MUTED }}>
          {eligibleCount} of {rows.length} tradeable
        </span>
        <label style={{ fontSize: 12, color: "var(--text)", cursor: "pointer", marginLeft: "auto" }}>
          <input type="checkbox" checked={onlyEligible}
                 onChange={(e) => setOnlyEligible(e.target.checked)}
                 style={{ marginRight: 5, verticalAlign: "middle" }} />
          hide blocked
        </label>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse",
                        fontSize: 12.5, fontVariantNumeric: "tabular-nums" }}>
          <thead>
            <tr>
              {th("Symbol", "symbol")}
              {th("Data")}
              {th("Regime")}
              {th("Expiry", "dte")}
              {th("Strike", "strike", true)}
              {th("Δ", "delta", true)}
              {th("Premium", "premium", true)}
              {th("Yield", "yield", true)}
              {th("OI", "oi", true)}
              {th("Spread", undefined, true)}
              {th("IV/HV", "ivhv", true)}
              {th("Status")}
            </tr>
          </thead>
          <tbody>
            {view.map((r) => {
              const q = quality(r);
              const code = reasonCode(r.blocks);
              const isOpen = open === r.symbol;
              return [
                <tr key={r.symbol}
                    onClick={() => setOpen(isOpen ? null : r.symbol)}
                    style={{ borderBottom: "1px solid var(--border)", cursor: "pointer",
                             background: r.eligible ? "rgba(12,163,12,.06)" : undefined }}>
                  <td style={{ padding: "7px 8px", fontWeight: 600 }}>{r.symbol}</td>
                  <td style={{ padding: "7px 8px", fontSize: 11 }}>
                    {r.provenance
                      ? <ProvenanceCell prov={r.provenance} />
                      : <span style={{ color: q.degraded ? WARN : MUTED }}>{q.label}</span>}
                  </td>
                  {/* Regime is PLAIN TEXT — see the note at the top of this file. */}
                  <td style={{ padding: "7px 8px", fontSize: 11, color: MUTED }}>{r.regime ?? "—"}</td>
                  <td style={{ padding: "7px 8px", whiteSpace: "nowrap" }}>
                    {r.expiry
                      ? (() => {
                          const e = String(r.expiry).replace(/-/g, "");
                          const shown = `${e.slice(0, 4)}-${e.slice(4, 6)}-${e.slice(6, 8)}`;
                          const monthly = r.expiry_kind === "monthly";
                          return (
                            <>
                              <span style={{ fontVariantNumeric: "tabular-nums" }}>{shown}</span>
                              <span title={monthly
                                     ? "standard monthly (3rd Friday) — deepest open interest, tightest spreads"
                                     : "weekly — faster decay, thinner book"}
                                    style={{ fontSize: 9, marginLeft: 5, padding: "1px 4px",
                                             borderRadius: 3, color: monthly ? OK : MUTED,
                                             border: `1px solid ${monthly ? OK : MUTED}55` }}>
                                {monthly ? "M" : "W"}
                              </span>
                              {r.dte != null && (
                                <span style={{ fontSize: 10, color: MUTED, marginLeft: 5 }}>{r.dte}d</span>
                              )}
                            </>
                          );
                        })()
                      : <span style={{ color: MUTED }}>—</span>}
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.suggested_strike, 2)}</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.suggested_delta, 2)}</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.suggested_premium, 2)}</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.annualized_yield_pct, 0, "%")}</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>
                    {r.open_interest === null ? <span style={{ color: MUTED }}>—</span> : r.open_interest}
                  </td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.spread_usd, 2)}</td>
                  <td style={{ padding: "7px 8px", textAlign: "right" }}>{num(r.iv_hv_ratio, 2)}</td>
                  <td style={{ padding: "7px 8px", whiteSpace: "nowrap" }}>
                    {/* Icon + WORD, never colour alone. */}
                    {r.eligible
                      ? <span style={{ color: OK, fontWeight: 600 }}>✓ tradeable</span>
                      : <span style={{ color: MUTED }}>✗ {code}</span>}
                  </td>
                </tr>,
                isOpen && (
                  <tr key={`${r.symbol}-why`} style={{ background: "var(--surface-2)" }}>
                    <td colSpan={12} style={{ padding: "10px 12px", fontSize: 12,
                                              color: "var(--text-muted)", lineHeight: 1.6 }}>
                      {/* The prose still exists and is worth reading — it just
                          does not shout at you 36 times at once. */}
                      {r.blocks?.length ? (
                        <div><b style={{ color: "var(--text)" }}>Why not:</b> {r.blocks.join(" ")}</div>
                      ) : <div><b style={{ color: OK }}>Passes every gate.</b></div>}
                      {r.warnings?.length ? (
                        <div style={{ marginTop: 6 }}>
                          <b style={{ color: WARN }}>Warnings:</b> {r.warnings.join(" ")}
                        </div>
                      ) : null}
                      {(onAnalyze || onRecord) && (
                        <div style={{ marginTop: 10, display: "flex", gap: 8 }}
                             onClick={(e) => e.stopPropagation()}>
                          {onAnalyze && (
                            <button onClick={() => onAnalyze(r)}
                                    style={{ padding: "4px 10px", borderRadius: 5, fontSize: 12,
                                             border: "1px solid var(--border)",
                                             background: "var(--surface-2)", color: "var(--text)",
                                             cursor: "pointer" }}>
                              Analyze
                            </button>
                          )}
                          {onRecord && (
                            <button disabled={busy} onClick={() => onRecord(r)}
                                    style={{ padding: "4px 10px", borderRadius: 5, fontSize: 12,
                                             border: `1px solid ${OK}66`, background: `${OK}14`,
                                             color: OK, cursor: busy ? "wait" : "pointer" }}>
                              Record CSP
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 10, fontSize: 11.5, color: MUTED, lineHeight: 1.6 }}>
        Click any row for the full reasoning. Reason codes:{" "}
        <b>regime</b> · <b>liquidity</b> · <b>extended</b> · <b>IV/HV</b> · <b>yield</b> ·{" "}
        <b>earnings</b> · <b>stale</b>.
        {" "}<span style={{ color: WARN }}>Amber</span> in the Data column means the price is
        carried or indicative, not live — the ranking below it is only as good as that input.
      </div>
    </div>
  );
}

const BAD = "#ec835a";
const SHORT_INPUT: Record<string, string> = {
  bars: "bars", spot: "spot", premium: "premium", iv: "IV",
  open_interest: "OI", div_yield: "div", earnings: "earnings",
};
const PROV_TONE: Record<ProvTrust, string> = {
  golden: OK,
  derived: MUTED,
  vendor: "var(--text-dim)",
  fallback: WARN,
  carried: WARN,
  unavailable: BAD,
};
const PROV_WORD: Record<ProvTrust, string> = {
  golden: "IBKR",
  derived: "computed",
  vendor: "vendor",
  fallback: "FALLBACK",
  carried: "CARRIED",
  unavailable: "MISSING",
};

function ProvenanceCell({ prov }: { prov: ProvenanceBlock | null | undefined }) {
  if (!prov) {
    return (
      <span style={{ color: BAD, fontSize: 11 }}
            title="This row carries no provenance block — it predates uniform provenance, or the screen failed to build one. Treat its numbers as unverified.">
        unknown
      </span>
    );
  }
  // A single worst-grade word does NOT discriminate. On 17 Aug every one of 82
  // rows read "MISSING", because open interest and dividend yield are dark
  // universe-wide — so the column cost a table width and told the reader
  // nothing about which row was worse than which. Show WHICH inputs are dark
  // and HOW MANY, so two rows with different gaps look different.
  const dark = prov.inputs.filter((i) => i.trust === "unavailable");
  const weak = prov.inputs.filter((i) => i.trust === "fallback" || i.trust === "carried");
  const tone = PROV_TONE[prov.worst] ?? BAD;
  // The hover ledger IS the explainer (house rule: every metric needs one).
  const ledger = prov.inputs
    .map((i) => `${i.label}: ${i.source_label}${i.age ? ` · ${i.age}` : ""}\n    ${i.detail}`)
    .join("\n");
  return (
    <span
      title={`WHERE THIS ROW'S NUMBERS CAME FROM\n\n${prov.summary}\n\n${ledger}\n\n`
        + `Grades — IBKR: the golden source. computed: derived by TradePro from `
        + `real inputs, reproducible by hand. vendor: the right non-broker feed `
        + `(no IBKR equivalent exists). FALLBACK: yahoo/IG standing in for a feed `
        + `IBKR does serve. CARRIED: a real number from an earlier moment. `
        + `MISSING: nobody served it.`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10,
        fontWeight: 700, color: tone, border: `1px solid ${tone}55`,
        background: `${tone}14`, borderRadius: 999, padding: "2px 8px",
        whiteSpace: "nowrap", cursor: "help",
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 999, background: tone, flex: "0 0 auto" }} />
      {/* Name the dark inputs rather than repeating one word on every row.
          "MISSING" told the reader nothing when all 82 rows said it; "OI, div"
          says exactly what this row is missing and lets two rows differ. */}
      {dark.length
        ? `no ${dark.map((i) => SHORT_INPUT[i.input] ?? i.input).join(", ")}`
        : weak.length
          ? `${PROV_WORD[prov.worst]} ${weak.map((i) => SHORT_INPUT[i.input] ?? i.input).join(", ")}`
          : PROV_WORD[prov.worst] ?? "?"}
    </span>
  );
}
