import { useCallback, useEffect, useState } from "react";
import { api, type OptionsPaperPosition, type RecordOptionsPositionBody } from "../../api/client";
import { OptionsPayoff, type PayoffSeed, type PayoffPlacement } from "./OptionsPayoff";

/**
 * Options Desk — the wheel (cash-secured put → assignment → covered call),
 * risk-first per the BRD. Two surfaces on one screen:
 *   1. CANDIDATE SCREEN — for each approved underlying, the regime (Ichimoku
 *      cloud), IV-Rank, liquidity, and whether it passes the risk engine for a
 *      cash-secured put. Eligibility is fail-visible: a name that can't be
 *      verified (missing IV-Rank / regime / chain) shows BLOCKED with the reason
 *      — never a silent green light (no false positives).
 *   2. PAPER LEDGER — the positions we've actually entered on paper, tracked
 *      through the wheel state machine (SHORT_PUT_OPEN → ASSIGNED →
 *      COVERED_CALL_OPEN → CLOSED) with realised P&L. The auditable per-cycle
 *      record the BRD requires before live capital.
 *
 * The screen is produced by the Mac-side `tradepro-options-screen` job (IV-Rank
 * from IBKR, regime from the bar cache, run through quant_engine.options.risk)
 * and pushed to /api/options/candidates — same pattern as bar-cache health.
 */
interface Candidate {
  symbol: string;
  regime: string | null;          // GREEN/YELLOW/ORANGE/RED
  iv_rank: number | null;         // 0-100 (only once our IV dataset window matures)
  iv: number | null;              // fraction
  // Vega-edge bridge (9 Aug 2026): IV ÷ HV30 + which gate variant applied
  // ("rank" | "bridge" | null) + how deep the IV dataset is, so the UI can
  // show a real number instead of a dead "n/a" column while rank accumulates.
  iv_hv_ratio?: number | null;
  iv_rank_days?: number | null;
  vega_gate?: "rank" | "bridge" | null;
  open_interest: number | null;
  spread_usd: number | null;
  eligible: boolean;              // passes the risk engine for a CSP
  blocks: string[];               // why-not (fail-visible)
  warnings: string[];
  suggested_strike: number | null;
  suggested_delta: number | null;
  suggested_premium: number | null;
  premium_source?: "live_mid" | "prev_close_indicative" | "carried_last_live" | null;
  premium_age_h?: number | null;   // how old a CARRIED premium is, in hours
  // TIER_SHORT (SPEC §1) — attempted ONLY when the standard ~35 DTE band would
  // hold through an earnings print. It ships to the live desk and was never
  // rendered here, so its verdict (usually a REASON IT DIDN'T FIRE) was
  // invisible: 15 Aug had 7 attempts and 0 candidates, and nothing said so.
  short_tier?: {
    status: string;
    detail?: string;
    strike?: number | null;
    premium?: number | null;
    dte?: number | null;
    eligible?: boolean;
  } | null;
  dte?: number | null;
  annualized_yield_pct?: number | null;  // ranking metric
  is_best?: boolean;                      // the single best eligible CSP
  // v1 §F0.3-4 — morning-candidates additions (2 Aug 2026): put-vs-buy
  // side-by-side and size-fit vs NAV. Both null until a real premium/spot
  // (put_vs_buy) or a reachable account NAV (size_fit_pct) exist — never
  // fabricated from partial data.
  put_vs_buy?: {
    buy_now_price: number;
    sell_put_strike: number;
    sell_put_premium: number;
    sell_put_effective_cost_if_assigned: number;
    discount_vs_buy_now_pct: number;
  } | null;
  size_fit_pct?: number | null;           // contract notional as % of account NAV
  ref_close?: number | null;              // last daily close — seeds the payoff spot
  spot_basis?: "daily_close" | "chain_spot" | null;  // WHICH number ref_close is
  forward_price?: number | null;          // F = S·e^((r−q)T) — the real OTM anchor at expiry
  forward_basis?: string | null;          // "r_and_div_yield" | "r_only_div_yield_unavailable"
  // Uniform provenance (15 Aug 2026) — one entry per INPUT behind the row, so
  // "is this cache, yahoo or IBKR?" is answerable at a glance instead of never.
  provenance?: {
    worst: ProvTrust;
    summary: string;
    inputs: ProvInput[];
  } | null;
}
type ProvTrust = "golden" | "derived" | "vendor" | "fallback" | "carried" | "unavailable";
interface ProvInput {
  input: string;
  label: string;
  source: string | null;
  source_label: string;
  trust: ProvTrust;
  detail: string;
  as_of: string | null;
  age: string | null;
}
interface ScreenResp {
  generated_at_utc: string | null;
  market_open: boolean;
  candidates: Candidate[];
  best_symbol?: string | null;
  eligible_count?: number;
  // Run-level data-health verdict (9 Aug 2026, owner rule: "if it's missing
  // some dataset we should make it loud and clear") — a data problem must
  // never be distinguishable from a market verdict only by reading 66 rows.
  data_health?: {
    degraded: boolean;
    iv_dark_count: number;
    no_chain_count: number;
    no_premium_count: number;
    symbols: number;
    summary: string;
    // Bar provenance rolled up across the run: a silent yahoo fallback is
    // invisible row-by-row across 82 symbols, so it is COUNTED here too.
    bar_sources?: Record<string, number> | null;
    fallback_bar_count?: number | null;
  } | null;
}

const FX_GBPUSD = 1.27;
const TONE = { ok: "#1D9E75", warn: "#E6A817", bad: "#D85A30", dim: "var(--text-muted)", line: "#4C9AFF" };
const REGIME_TONE: Record<string, string> = { GREEN: TONE.ok, YELLOW: TONE.warn, ORANGE: "#E67E22", RED: TONE.bad };
const STATE_TONE: Record<string, string> = {
  SHORT_PUT_OPEN: TONE.warn, ASSIGNED: "#E67E22", COVERED_CALL_OPEN: TONE.warn, CLOSED: TONE.dim,
};

export function OptionsDesk() {
  const [data, setData] = useState<ScreenResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [positions, setPositions] = useState<OptionsPaperPosition[]>([]);
  const [posErr, setPosErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [seed, setSeed] = useState<PayoffSeed | undefined>(undefined);

  type WatchdogResp = Awaited<ReturnType<typeof api.optionsWatchdog>>;
  const [watchdog, setWatchdog] = useState<WatchdogResp | null>(null);
  const [watchdogErr, setWatchdogErr] = useState<string | null>(null);

  const loadPositions = useCallback(() => {
    api.optionsPositions()
      .then((d) => { setPositions(d.positions ?? []); setPosErr(null); })
      .catch((e) => setPosErr(String(e?.message || e)));
  }, []);

  const loadWatchdog = useCallback(() => {
    api.optionsWatchdog()
      .then((d) => { setWatchdog(d); setWatchdogErr(null); })
      .catch((e) => setWatchdogErr(String(e?.message || e)));
  }, []);

  useEffect(() => {
    let live = true;
    api.optionsCandidates()
      .then((d) => { if (live) { setData(d as ScreenResp); setErr(null); } })
      .catch((e) => { if (live) setErr(String(e?.message || e)); })
      .finally(() => { if (live) setLoading(false); });
    loadPositions();
    loadWatchdog();
    return () => { live = false; };
  }, [loadPositions, loadWatchdog]);

  const record = useCallback(async (body: RecordOptionsPositionBody) => {
    setBusy(true);
    try {
      await api.recordOptionsPosition(body);
      loadPositions();
      loadWatchdog();
    } catch (e) {
      setPosErr(String((e as Error)?.message || e));
    } finally {
      setBusy(false);
    }
  }, [loadPositions, loadWatchdog]);

  const recordCandidate = useCallback((c: Candidate) => {
    if (c.suggested_strike == null) return;
    const cash = Math.round((c.suggested_strike * 100) / FX_GBPUSD);
    record({
      symbol: c.symbol,
      structure: "CASH_SECURED_PUT",
      state: "SHORT_PUT_OPEN",
      strike: c.suggested_strike,
      delta: c.suggested_delta,
      ivRank: c.iv_rank,
      premium: c.suggested_premium,
      contracts: 1,
      cashSecuredGbp: cash,
      regime: c.regime,
      notes: "recorded from screen",
      riskDecisionJson: JSON.stringify({ eligible: c.eligible, blocks: c.blocks, warnings: c.warnings }),
    });
  }, [record]);

  const transition = useCallback(async (id: number, state: string, pnl?: number, notes?: string) => {
    setBusy(true);
    try {
      await api.optionsPositionEvent(id, { state, realisedPnlGbp: pnl ?? null, notes: notes ?? null });
      loadPositions();
      loadWatchdog();
    } catch (e) {
      setPosErr(String((e as Error)?.message || e));
    } finally {
      setBusy(false);
    }
  }, [loadPositions, loadWatchdog]);

  const removePosition = useCallback(async (id: number, symbol: string) => {
    if (!window.confirm(`Delete paper position ${symbol} (#${id})? This can't be undone.`)) return;
    setBusy(true);
    try {
      await api.deleteOptionsPosition(id);
      loadPositions();
      loadWatchdog();
    } catch (e) {
      setPosErr(String((e as Error)?.message || e));
    } finally {
      setBusy(false);
    }
  }, [loadPositions, loadWatchdog]);

  const placeFromExplorer = useCallback((p: PayoffPlacement) => {
    const lbl = p.structure === "CASH_SECURED_PUT" ? "cash-secured put" : "covered call";
    if (!window.confirm(
      `Place paper ${lbl} — ${p.symbol} $${p.strike} ×${p.contracts}\n` +
      `Premium $${p.premium} · max gain £${p.maxGainGbp.toLocaleString()} · max loss £${p.maxLossGbp.toLocaleString()} · PoP ${(p.pop * 100).toFixed(0)}%\n\n` +
      `This records the trade on the paper ledger.`
    )) return;
    record({
      symbol: p.symbol,
      structure: p.structure,
      state: p.structure === "CASH_SECURED_PUT" ? "SHORT_PUT_OPEN" : "COVERED_CALL_OPEN",
      strike: p.strike,
      delta: null,
      ivRank: null,
      premium: p.premium,
      contracts: p.contracts,
      cashSecuredGbp: p.capitalGbp,
      notes: `placed from payoff explorer · DTE ${p.dte} · BE $${p.breakevenUsd}`,
      riskDecisionJson: JSON.stringify({
        source: "payoff-explorer", dte: p.dte, breakeven_usd: p.breakevenUsd,
        max_gain_gbp: p.maxGainGbp, max_loss_gbp: p.maxLossGbp, pop: p.pop, iv: p.iv,
      }),
    });
  }, [record]);

  // Analyze opens a MODAL (owner: the bottom-of-page chart was unusable) —
  // bigger canvas, taller chart, Esc / backdrop / ✕ to close. Seeded with the
  // candidate's REAL values (spot from ref_close, its actual DTE).
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  const analyze = useCallback((c: Candidate) => {
    setSeed({
      symbol: c.symbol,
      structure: "CASH_SECURED_PUT",
      strike: c.suggested_strike,
      premium: c.suggested_premium,
      spot: c.ref_close ?? null,
      contracts: 1,
      dte: c.dte ?? 35,
    });
    setAnalyzeOpen(true);
  }, []);
  useEffect(() => {
    if (!analyzeOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setAnalyzeOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [analyzeOpen]);

  const rawCands = data?.candidates ?? [];
  // Eligible first (by annualised yield); then NEAR-MISSES — fewest blocks,
  // best yield — so with 66 names the top of the table is signal, not an
  // alphabetical wall of identical rejections.
  const cands = [...rawCands].sort((a, b) => {
    if (a.eligible !== b.eligible) return a.eligible ? -1 : 1;
    if (a.eligible && b.eligible) return (b.annualized_yield_pct ?? 0) - (a.annualized_yield_pct ?? 0);
    const ab = a.blocks?.length ?? 99, bb = b.blocks?.length ?? 99;
    if (ab !== bb) return ab - bb;
    return (b.annualized_yield_pct ?? 0) - (a.annualized_yield_pct ?? 0);
  });
  const eligible = cands.filter((c) => c.eligible);
  const best = cands.find((c) => c.is_best) ?? null;
  const open = positions.filter((p) => p.state !== "CLOSED");
  // Names where we actually HOLD STOCK. Only ASSIGNED and COVERED_CALL_OPEN
  // qualify: an open SHORT_PUT is the obligation to buy, not the shares, and a
  // covered call written against it would be a naked call.
  const shareSymbols = positions
    .filter((p) => p.state === "ASSIGNED" || p.state === "COVERED_CALL_OPEN")
    .map((p) => p.symbol);
  const realised = positions.reduce((s, p) => s + (p.realised_pnl_gbp ?? 0), 0);

  return (
    <div style={{ padding: "8px 4px" }}>
      <MarketDataBanner />
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>Options Desk — the Wheel</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5, marginTop: 2 }}>
          Cash-secured puts on quality names, risk-first. A candidate is <b>eligible</b> only when it
          clears <b>every</b> gate: constructive regime (in/above the Ichimoku cloud, not a falling knife),
          a <b>vega edge</b> (IV-Rank &gt; 30 once our IV dataset matures; until then the IV/HV&nbsp;≥&nbsp;1.0
          bridge — implied must at least pay for realised vol), delta 0.20–0.35, 25–50 DTE, OI ≥ 250,
          spread within the premium-relative cap, a <b>premium floor</b> (≥ $0.20 and ≥ 8%/yr annualised —
          no selling for pennies), no earnings in the window (ETFs structurally exempt), and within capital
          limits. Anything we can't verify shows <b>BLOCKED with the reason</b> — hover a row's why-not for
          the full list. Never a silent green light.
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <Stat label="Eligible CSP" value={eligible.length} tone="ok" />
        <Stat label="Screened" value={cands.length} tone="dim" />
        <Stat label="Open paper" value={open.length} tone="warn" />
        <Stat label="Realised £" value={realised.toFixed(0)} tone={realised >= 0 ? "ok" : "bad"} />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {data?.generated_at_utc ? `last screen ${new Date(data.generated_at_utc).toLocaleString()}` : "no screen yet"}
          {data && !data.market_open ? " · market closed (chain/Δ pending open)" : ""}
        </span>
      </div>

      {/* ── DATA HEALTH — loud when the run itself is degraded ──── */}
      {data?.data_health?.degraded && (
        <div style={{
          marginBottom: 12, padding: "10px 14px", borderRadius: 8,
          border: `1px solid ${TONE.warn}`, background: `${TONE.warn}14`,
          color: TONE.warn, fontSize: 12, lineHeight: 1.5, fontWeight: 600,
        }}>
          ⚠ {data.data_health.summary}
        </div>
      )}

      {/* ── Morning candidates (v1 F0.3-4) ─────────────────────── */}
      {/* Action-first: merit-ranked eligible names, one line each, so the
          2-minute morning loop doesn't require reading the full data table
          below. Never capital-gated here (project_wheel_signal_vs_paper_
          capital_split) — size-fit is informational, shown not filtered. */}
      {!loading && !err && cands.length > 0 && (
        <MorningCandidatesPanel candidates={eligible.slice(0, 5)} all={cands} onAnalyze={analyze} onRecord={recordCandidate} busy={busy} />
      )}

      {/* ── Candidate screen ───────────────────────────────────── */}
      <SectionTitle>Full candidate screen</SectionTitle>
      {!loading && !err && cands.length > 0 && <BestPick best={best} onAnalyze={analyze} onRecord={recordCandidate} busy={busy} />}
      {loading && <div style={{ color: "var(--text-muted)", padding: 16 }}>Loading screen…</div>}
      {err && <div style={{ color: TONE.bad, padding: 16 }}>Screen unavailable: {err}</div>}
      {!loading && !err && cands.length === 0 && (
        <div style={{ color: "var(--text-muted)", padding: 16 }}>
          No candidates screened yet. Run the Mac screen job (<code>tradepro-options-screen</code>) to populate.
        </div>
      )}

      {cands.length > 0 && (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8, marginBottom: 18 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Data", "Regime", "Vega edge", "OI / Spread", "Eligible (CSP)", "Annual yield", "Suggested", "Put vs buy now", "Size fit", "Why / why-not", ""].map((h) => (
                  <th key={h} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cands.map((c) => (
                <tr key={c.symbol} style={{ borderTop: "1px solid #141b2b", background: c.is_best ? `${TONE.ok}12` : undefined }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    {c.is_best && <span title="best eligible CSP right now" style={{ marginRight: 5 }}>⭐</span>}{c.symbol}
                    {/* Short-dated tier verdict. Only present when earnings
                        conflict with the standard band — and usually it is a
                        REASON IT DIDN'T FIRE, which is exactly the thing worth
                        showing rather than hiding. */}
                    {c.short_tier && (
                      <span
                        title={`SHORT-DATED TIER (7-21 DTE, earnings avoidance)\n\n${c.short_tier.status}\n${c.short_tier.detail ?? ""}\n\nThis tier is attempted only when the standard ~35 DTE expiry would hold through an earnings print. A status other than a live suggestion means it could NOT find a safe short-dated alternative — the name simply stays blocked on earnings.`}
                        style={{ display: "block", marginTop: 2, fontSize: 9, fontWeight: 600,
                                 cursor: "help",
                                 color: c.short_tier.eligible ? TONE.ok : TONE.dim }}
                      >
                        {c.short_tier.eligible ? "◆ SHORT-DATED" : `◇ short: ${c.short_tier.status.replace(/_/g, " ")}`}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <ProvenanceCell prov={c.provenance} />
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {c.regime ? <RegimePill regime={c.regime} /> : <span style={{ color: TONE.bad, fontSize: 11 }}>n/a</span>}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {c.iv_rank != null ? (
                      <IvGauge rank={c.iv_rank} />
                    ) : c.iv_hv_ratio != null ? (
                      <span
                        title={`IV/HV bridge gate: implied vol ÷ 30d realised. ≥ 1.00 = premium at least pays for realised risk. `
                          + `Used while our own IV dataset (${c.iv_rank_days ?? "?"}d so far) grows toward the 60d needed for a true IV-Rank.`}
                        style={{ fontFamily: "var(--font-mono)", fontWeight: 600,
                                 color: c.iv_hv_ratio >= 1 ? TONE.ok : "var(--text-dim)" }}
                      >
                        {c.iv_hv_ratio.toFixed(2)}<span style={{ color: "var(--text-muted)", fontWeight: 400, fontSize: 10 }}> IV/HV</span>
                      </span>
                    ) : (
                      <span title="No vega-edge metric available — IV snapshot dark; blocked, not assumed."
                            style={{ color: TONE.bad, fontSize: 11 }}>n/a</span>
                    )}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {c.open_interest == null ? "—" : c.open_interest.toLocaleString()}
                    {c.spread_usd != null ? ` / $${c.spread_usd.toFixed(2)}` : ""}
                  </td>
                  <td style={{ padding: "8px 10px", fontWeight: 700, color: c.eligible ? TONE.ok : TONE.dim }}>
                    {c.eligible ? "✓ YES" : "— no"}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 600, color: c.annualized_yield_pct != null ? (c.eligible ? TONE.ok : "var(--text-dim)") : "var(--text-muted)" }}>
                    {c.annualized_yield_pct != null ? `${c.annualized_yield_pct.toFixed(0)}%/yr` : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {c.suggested_strike != null ? (
                      <span>
                        {`$${c.suggested_strike} · Δ${(c.suggested_delta ?? 0).toFixed(2)}${c.suggested_premium != null ? ` · $${c.suggested_premium.toFixed(2)}` : ""}`}
                        {/* EVERY pricing state gets a badge, not just one. The
                            15 Aug board was 55 prev-close, 19 CARRIED and only
                            8 live — and `carried_last_live`, the stalest of the
                            three, rendered with no label at all, so a number
                            hours old looked identical to a live quote. */}
                        {c.premium_source && c.premium_source !== "live_mid" && (
                          <span
                            title={c.premium_source === "prev_close_indicative"
                              ? "Options have no pre-market session — this premium is the PRIOR session's close (IBKR field 7741), shown as the last available value. Indicative only; live quotes take over at the US open."
                              : `Pricing CARRIED WHOLESALE from the last screen that had live quotes${c.premium_age_h != null ? ` — ${c.premium_age_h}h old` : ""}. Strike, premium, OI and spread all belong to that earlier snapshot, so nothing is grafted across moments. Hard-blocked from eligibility: informative, never actionable.`}
                            style={{ marginLeft: 4, fontSize: 9, padding: "1px 4px", borderRadius: 4,
                                     border: `1px solid ${c.premium_source === "carried_last_live" ? TONE.bad : TONE.warn}`,
                                     color: c.premium_source === "carried_last_live" ? TONE.bad : TONE.warn }}
                          >
                            {c.premium_source === "prev_close_indicative"
                              ? "prev close"
                              : `carried${c.premium_age_h != null ? ` ${c.premium_age_h}h` : ""}`}
                          </span>
                        )}
                        {c.forward_price != null && (
                          <span
                            style={{ display: "block", fontSize: 10, color: "var(--text-muted)" }}
                            title={`Forward price at expiry (F = S·e^((r−q)T)) — the honest "how far OTM" anchor: `
                              + `strike vs forward, not vs spot. `
                              + (c.forward_basis === "r_and_div_yield"
                                ? "Includes this name's dividend yield."
                                : "Rates-only (dividend yield not served) — slightly overstated for dividend payers.")}
                          >
                            fwd ${c.forward_price.toFixed(1)}
                            {c.suggested_strike != null && c.forward_price > 0 &&
                              ` · strike ${((1 - c.suggested_strike / c.forward_price) * 100).toFixed(1)}% below fwd`}
                          </span>
                        )}
                      </span>
                    ) : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {c.put_vs_buy ? (
                      <span title={
                        `Buy now: $${c.put_vs_buy.buy_now_price.toFixed(2)} · `
                        + `Sell put: $${c.put_vs_buy.sell_put_strike} strike, `
                        + `$${c.put_vs_buy.sell_put_premium.toFixed(2)} premium, `
                        + `effective cost if assigned $${c.put_vs_buy.sell_put_effective_cost_if_assigned.toFixed(2)}`
                      }>
                        <span style={{ color: "var(--text-muted)" }}>buy </span>${c.put_vs_buy.buy_now_price.toFixed(0)}
                        <span style={{ color: "var(--text-muted)" }}> vs put </span>${c.put_vs_buy.sell_put_effective_cost_if_assigned.toFixed(0)}
                        <span style={{ color: c.put_vs_buy.discount_vs_buy_now_pct >= 0 ? TONE.ok : TONE.bad, marginLeft: 4 }}>
                          ({c.put_vs_buy.discount_vs_buy_now_pct >= 0 ? "-" : "+"}{Math.abs(c.put_vs_buy.discount_vs_buy_now_pct).toFixed(1)}%)
                        </span>
                      </span>
                    ) : (
                      <span style={{ color: "var(--text-muted)", fontSize: 11 }}
                            title="This comparison needs a live option premium; chains are cold when the market is closed. It fills in-session.">
                        needs live premium
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}
                      title="Contract notional as a share of account NAV — informational only, never a hard gate here (the risk engine's own notional cap does that)">
                    {c.size_fit_pct != null ? `${c.size_fit_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td
                    style={{ padding: "8px 10px", color: c.eligible ? TONE.warn : TONE.bad,
                             maxWidth: 360, minWidth: 240, fontSize: 11, lineHeight: 1.45,
                             // Wrap up to 3 lines — single-line ellipsis HID the
                             // reason entirely at narrow widths (owner: "text
                             // gets hidden, I can't see them"). Full list stays
                             // in the hover tooltip.
                             display: "-webkit-box", WebkitLineClamp: 3,
                             WebkitBoxOrient: "vertical", overflow: "hidden",
                             whiteSpace: "normal", overflowWrap: "anywhere" }}
                    title={[...(c.blocks ?? []), ...(c.warnings ?? [])].join("\n") || undefined}
                  >
                    {(() => {
                      // PRIMARY reason wrapped-visible; the rest in the tooltip.
                      const list = c.blocks?.length ? c.blocks : (c.warnings ?? []);
                      if (!list.length) return c.eligible ? "all gates pass" : "—";
                      const extra = list.length - 1;
                      return <>{list[0]}{extra > 0 && <span style={{ color: "var(--text-muted)" }}> +{extra} more (hover)</span>}</>;
                    })()}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        disabled={c.suggested_strike == null}
                        onClick={() => analyze(c)}
                        title={c.suggested_strike == null ? "no chain yet (pending market open)" : "load into the payoff explorer"}
                        style={btnStyle(c.suggested_strike != null, TONE.line)}
                      >Analyze</button>
                      <button
                        disabled={busy || c.suggested_strike == null}
                        onClick={() => recordCandidate(c)}
                        title={c.suggested_strike == null ? "no suggested strike yet (chain pending market open)" : "record this CSP on the paper ledger"}
                        style={btnStyle(c.suggested_strike != null)}
                      >Record CSP</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Analyze modal — big, zoomable payoff for one candidate ── */}
      {analyzeOpen && (
        <div
          onClick={() => setAnalyzeOpen(false)}
          style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.65)",
                   display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ width: "min(1100px, 96vw)", maxHeight: "92vh", overflowY: "auto",
                     background: "var(--surface)", border: "1px solid var(--border)",
                     borderRadius: 12, padding: 16, boxShadow: "0 18px 60px rgba(0,0,0,0.5)" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>
                Analyze{seed?.symbol ? <span style={{ fontFamily: "var(--font-mono)" }}> · {seed.symbol}</span> : ""} — payoff &amp; greeks
              </div>
              <button onClick={() => setAnalyzeOpen(false)} title="Close (Esc)"
                      style={{ background: "transparent", border: "1px solid var(--border)", borderRadius: 6,
                               color: "var(--text-dim)", padding: "4px 10px", cursor: "pointer", fontSize: 13 }}>✕</button>
            </div>
            <OptionsPayoff seed={seed} onPlace={placeFromExplorer} placing={busy} chartHeight={430} shareSymbols={shareSymbols} />
          </div>
        </div>
      )}

      {/* ── Payoff explorer (manual entry) ─────────────────────── */}
      <div id="options-payoff" style={{ marginBottom: 18 }}>
        <SectionTitle>Payoff explorer — max gain / loss / breakeven · place a paper trade</SectionTitle>
        <OptionsPayoff onPlace={placeFromExplorer} placing={busy} shareSymbols={shareSymbols} />
      </div>

      {/* ── Position watchdog ──────────────────────────────────── */}
      {/* v1 F0.1 + BABA addendum: expiry clock + assignment-risk + dead-
          collateral for every open position, so the trader sees "does this
          need attention today" without reading every row. Hidden entirely
          when there's nothing open and nothing errored — an empty watchdog
          on an empty ledger isn't worth a panel. */}
      {(watchdog && watchdog.count > 0) || watchdogErr ? (
        <div style={{ marginBottom: 18 }}>
          <SectionTitle>
            Position watchdog
            {watchdog && watchdog.needsAttention > 0 && (
              <span style={{ marginLeft: 8, color: TONE.bad, fontWeight: 700 }}>
                {watchdog.needsAttention} need{watchdog.needsAttention === 1 ? "s" : ""} attention
              </span>
            )}
          </SectionTitle>
          {watchdogErr && <div style={{ color: TONE.bad, padding: "8px 0" }}>Watchdog unavailable: {watchdogErr}</div>}
          {/* SCOPE, STATED (16 Aug 2026). This watches `options_paper_position`
              — the PAPER LEDGER — not the broker. Owner's standing rule is
              "broker is golden source for positions; never trust the OMS for
              'do we own X'", and the owner has decided NOT to wire live
              positions into this platform for now. That decision is fine; what
              is not fine is a panel that shows one position and reads as a
              complete book. Verified against the live IBKR account the same
              day: SIX open option positions existed, the ledger held ONE. So
              the panel must never imply completeness. */}
          <div style={{
            fontSize: 11, color: TONE.warn, border: `1px solid ${TONE.warn}55`,
            background: `${TONE.warn}12`, borderRadius: 6, padding: "6px 10px",
            margin: "6px 0 10px",
          }}>
            <b>PAPER LEDGER ONLY</b> — this watches positions recorded here, not your
            broker account. Live positions you hold elsewhere are <b>not monitored</b>:
            no expiry clock, no assignment warning. Treat this as a record of what was
            recorded, never as your book.
          </div>
          {watchdog && watchdog.positions.length > 0 && (
            <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                    {["Symbol", "Structure", "Strike", "Expiry", "Spot", "Moneyness", "Flags"].map((h) => (
                      <th key={h} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {watchdog.positions.map((w) => {
                    const urgencyTone = w.expiryUrgency === "expired" || w.expiryUrgency === "urgent"
                      ? TONE.bad : w.expiryUrgency === "warn" ? TONE.warn : "var(--text-dim)";
                    return (
                      <tr key={w.id} style={{ borderTop: "1px solid #141b2b" }}>
                        <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{w.symbol}</td>
                        <td style={{ padding: "8px 10px", color: "var(--text-dim)" }}>{w.structure}</td>
                        <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}>{w.strike != null ? `$${w.strike}` : "—"}</td>
                        <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: urgencyTone }}
                            title={w.expiryUrgency}>
                          {w.expiry ?? "—"}{w.daysToExpiry != null ? ` (${w.daysToExpiry}d)` : ""}
                        </td>
                        <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)" }}
                            title={w.spotError ?? undefined}>
                          {w.spot != null ? `$${w.spot}` : (w.spotError ? "n/a" : "—")}
                        </td>
                        <td style={{ padding: "8px 10px", color: w.moneyness?.startsWith("ITM") ? TONE.bad : "var(--text-dim)" }}>
                          {w.moneyness ?? "—"}
                          {w.distancePct != null ? ` (${w.distancePct > 0 ? "+" : ""}${w.distancePct}%)` : ""}
                        </td>
                        <td style={{ padding: "8px 10px" }}>
                          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                            {(w.expiryUrgency === "urgent" || w.expiryUrgency === "expired") && (
                              <Flag tone={TONE.bad}>{w.expiryUrgency === "expired" ? "expired" : "expiry soon"}</Flag>
                            )}
                            {w.moneyness?.startsWith("ITM") && <Flag tone={TONE.bad}>assignment risk</Flag>}
                            {w.deadCollateral && <Flag tone={TONE.warn}>dead collateral</Flag>}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}

      {/* ── Paper ledger ───────────────────────────────────────── */}
      <SectionTitle>Paper ledger — wheel positions</SectionTitle>
      <ManualRecord onRecord={record} busy={busy} />
      {posErr && <div style={{ color: TONE.bad, padding: "8px 0" }}>Ledger error: {posErr}</div>}
      {positions.length === 0 && !posErr && (
        <div style={{ color: "var(--text-muted)", padding: "8px 2px" }}>
          No paper positions yet. Record one from an eligible candidate above, or add a manual entry.
        </div>
      )}
      {positions.length > 0 && (
        <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                {["Symbol", "Structure", "State", "Strike / Δ", "Premium", "Cash £", "Opened", "Realised £", "Actions"].map((h) => (
                  <th key={h} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id} style={{ borderTop: "1px solid #141b2b" }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>{p.symbol}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-dim)" }}>{p.structure.replace(/_/g, " ").toLowerCase()}</td>
                  <td style={{ padding: "8px 10px", fontWeight: 600, color: STATE_TONE[p.state] || TONE.dim }}>{p.state.replace(/_/g, " ")}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                    {p.strike != null ? `$${p.strike}` : "—"}{p.delta != null ? ` · Δ${p.delta.toFixed(2)}` : ""}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{p.premium != null ? `$${p.premium.toFixed(2)}` : "—"}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>{p.cash_secured_gbp != null ? `£${p.cash_secured_gbp.toLocaleString()}` : "—"}</td>
                  <td style={{ padding: "8px 10px", color: "var(--text-muted)", whiteSpace: "nowrap" }}>{new Date(p.opened_at_utc).toLocaleDateString()}</td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", fontWeight: 600, color: p.realised_pnl_gbp == null ? TONE.dim : p.realised_pnl_gbp >= 0 ? TONE.ok : TONE.bad }}>
                    {p.realised_pnl_gbp == null ? "—" : `£${p.realised_pnl_gbp.toFixed(0)}`}
                  </td>
                  <td style={{ padding: "8px 10px", whiteSpace: "nowrap" }}>
                    <div style={{ display: "flex", gap: 6 }}>
                      {p.state === "SHORT_PUT_OPEN" && (
                        <button disabled={busy} onClick={() => transition(p.id, "ASSIGNED", undefined, "assigned — now hold shares")} style={btnStyle(true)}>Assigned</button>
                      )}
                      {p.state === "ASSIGNED" && (
                        <button disabled={busy} onClick={() => transition(p.id, "COVERED_CALL_OPEN", undefined, "sold covered call")} style={btnStyle(true)}>Sell CC</button>
                      )}
                      {p.state !== "CLOSED" && (
                        <button
                          disabled={busy}
                          onClick={() => {
                            const v = window.prompt(`Close ${p.symbol} — realised P&L in £ (e.g. 85 or -120):`, "");
                            if (v == null) return;
                            const pnl = Number(v);
                            if (Number.isNaN(pnl)) { setPosErr("P&L must be a number"); return; }
                            transition(p.id, "CLOSED", pnl, "closed");
                          }}
                          style={btnStyle(true, TONE.bad)}
                        >Close</button>
                      )}
                      <button disabled={busy} onClick={() => removePosition(p.id, p.symbol)} title="delete this paper position" style={btnStyle(true, TONE.dim)}>🗑</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ManualRecord({ onRecord, busy }: { onRecord: (b: RecordOptionsPositionBody) => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ symbol: "", strike: "", premium: "", delta: "", contracts: "1", expiry: "", notes: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  if (!open) {
    return (
      <div style={{ margin: "6px 0 12px" }}>
        <button onClick={() => setOpen(true)} style={btnStyle(true)}>+ Record paper CSP (manual)</button>
      </div>
    );
  }
  const strike = Number(f.strike);
  const valid = f.symbol.trim() !== "" && !Number.isNaN(strike) && strike > 0;
  return (
    <div style={{ margin: "8px 0 14px", padding: 12, border: "1px solid var(--border)", borderRadius: 8, background: "var(--surface-2)" }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <Field label="Symbol" v={f.symbol} on={set("symbol")} w={70} mono />
        <Field label="Strike $" v={f.strike} on={set("strike")} w={70} mono />
        <Field label="Premium $" v={f.premium} on={set("premium")} w={80} mono />
        <Field label="Delta" v={f.delta} on={set("delta")} w={60} mono />
        <Field label="Contracts" v={f.contracts} on={set("contracts")} w={70} mono />
        <Field label="Expiry" v={f.expiry} on={set("expiry")} w={110} placeholder="YYYY-MM-DD" />
        <Field label="Notes" v={f.notes} on={set("notes")} w={180} />
        <button
          disabled={busy || !valid}
          onClick={() => {
            const cash = Math.round((strike * 100 * (Number(f.contracts) || 1)) / FX_GBPUSD);
            onRecord({
              symbol: f.symbol.trim().toUpperCase(),
              structure: "CASH_SECURED_PUT",
              state: "SHORT_PUT_OPEN",
              strike,
              premium: f.premium ? Number(f.premium) : null,
              delta: f.delta ? Number(f.delta) : null,
              contracts: Number(f.contracts) || 1,
              expiry: f.expiry || null,
              cashSecuredGbp: cash,
              notes: f.notes || "manual entry",
            });
            setF({ symbol: "", strike: "", premium: "", delta: "", contracts: "1", expiry: "", notes: "" });
            setOpen(false);
          }}
          style={btnStyle(valid)}
        >Record</button>
        <button onClick={() => setOpen(false)} style={btnStyle(true, TONE.dim)}>Cancel</button>
      </div>
    </div>
  );
}

function Field({ label, v, on, w, mono, placeholder }: { label: string; v: string; on: (e: React.ChangeEvent<HTMLInputElement>) => void; w: number; mono?: boolean; placeholder?: string }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 3, fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
      {label}
      <input
        value={v} onChange={on} placeholder={placeholder}
        style={{ width: w, padding: "5px 7px", fontSize: 12, borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)", fontFamily: mono ? "var(--font-mono)" : "inherit" }}
      />
    </label>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-dim)", margin: "4px 0 8px", textTransform: "uppercase", letterSpacing: "0.05em" }}>{children}</div>;
}

function btnStyle(enabled: boolean, color = TONE.ok): React.CSSProperties {
  return {
    padding: "5px 10px", fontSize: 11, fontWeight: 600, borderRadius: 6,
    border: `1px solid ${enabled ? color : "var(--border)"}`,
    background: enabled ? `${color}1a` : "transparent",
    color: enabled ? color : "var(--text-muted)",
    cursor: enabled ? "pointer" : "not-allowed", whiteSpace: "nowrap",
  };
}

function MorningCandidatesPanel({ candidates, all, onAnalyze, onRecord, busy }: {
  candidates: Candidate[]; all: Candidate[]; onAnalyze: (c: Candidate) => void; onRecord: (c: Candidate) => void; busy: boolean;
}) {
  // Market-level verdict + nearest-to-eligible: "0 eligible" with no context
  // reads as a broken screen. Say WHAT the market is doing (median IV/HV) and
  // WHO is closest to clearing, with the one thing in the way.
  const ratios = all.map((c) => c.iv_hv_ratio).filter((r): r is number => r != null).sort((a, b) => a - b);
  const medianRatio = ratios.length ? ratios[Math.floor(ratios.length / 2)] : null;
  const nearMisses = [...all]
    .filter((c) => !c.eligible && (c.blocks?.length ?? 0) > 0)
    .sort((a, b) => (a.blocks!.length - b.blocks!.length)
      || ((b.annualized_yield_pct ?? 0) - (a.annualized_yield_pct ?? 0)))
    .slice(0, 3);
  return (
    <div style={{ marginBottom: 18 }}>
      <SectionTitle>
        Today's candidates
        <span style={{ marginLeft: 8, fontWeight: 400, color: "var(--text-muted)", textTransform: "none" }}>
          {candidates.length === 0 ? "none eligible right now" : `top ${candidates.length}, ranked by annualised yield`}
        </span>
      </SectionTitle>
      {candidates.length === 0 ? (
        <div style={{ padding: "12px 14px", border: "1px dashed var(--border)", borderRadius: 8, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: "var(--text)" }}>
            <b>No trade is the verdict, not a failure.</b>{" "}
            {medianRatio != null
              ? <>Premium is {medianRatio < 1 ? "THIN" : "mixed"} across the universe (median IV/HV {medianRatio.toFixed(2)}
                 {medianRatio < 1 ? " — sellers aren't being paid for realised risk; the correct wheel action is to wait" : ""}).</>
              : <>Vega-edge data is dark (market closed) — gates re-evaluate in-session.</>}
          </div>
          {nearMisses.length > 0 && (
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              <span style={{ textTransform: "uppercase", fontSize: 10, letterSpacing: "0.05em", color: "var(--text-muted)" }}>Closest to clearing: </span>
              {nearMisses.map((c, i) => (
                <span key={c.symbol}>
                  {i > 0 && " · "}
                  <b style={{ fontFamily: "var(--font-mono)" }}>{c.symbol}</b>
                  <span style={{ color: "var(--text-muted)" }}> ({c.blocks!.length} gate{c.blocks!.length > 1 ? "s" : ""}: {c.blocks![0].split("—")[0].trim()}{c.blocks!.length > 1 ? ", …" : ""})</span>
                </span>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {candidates.map((c) => (
            <div key={c.symbol} style={{
              display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
              borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface-2)", flexWrap: "wrap",
            }}>
              <span style={{ fontWeight: 700, fontFamily: "var(--font-mono)", minWidth: 56 }}>{c.symbol}</span>
              <span style={{ fontSize: 12 }}>
                SELL PUT ${c.suggested_strike} · Δ{(c.suggested_delta ?? 0).toFixed(2)}
                {c.suggested_premium != null ? ` · $${c.suggested_premium.toFixed(2)} premium` : ""}
              </span>
              {c.annualized_yield_pct != null && (
                <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", fontWeight: 600, color: TONE.ok }}>
                  {c.annualized_yield_pct.toFixed(0)}%/yr
                </span>
              )}
              {c.size_fit_pct != null && (
                <span style={{ fontSize: 11, color: "var(--text-muted)" }} title="contract notional as % of account NAV — informational, not a gate">
                  {c.size_fit_pct.toFixed(1)}% of NAV
                </span>
              )}
              {c.put_vs_buy && (
                <span style={{ fontSize: 11, color: c.put_vs_buy.discount_vs_buy_now_pct >= 0 ? TONE.ok : TONE.dim }}
                      title={`Buy now $${c.put_vs_buy.buy_now_price} vs effective cost if assigned $${c.put_vs_buy.sell_put_effective_cost_if_assigned}`}>
                  {c.put_vs_buy.discount_vs_buy_now_pct >= 0 ? "-" : "+"}{Math.abs(c.put_vs_buy.discount_vs_buy_now_pct).toFixed(1)}% vs buying now
                </span>
              )}
              <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                <button onClick={() => onAnalyze(c)} style={btnStyle(true, TONE.line)}>Analyze</button>
                <button disabled={busy} onClick={() => onRecord(c)} style={btnStyle(!busy)}>Record CSP</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BestPick({ best, onAnalyze, onRecord, busy }: { best: Candidate | null; onAnalyze: (c: Candidate) => void; onRecord: (c: Candidate) => void; busy: boolean }) {
  if (!best) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", marginBottom: 12, borderRadius: 8, border: "1px dashed var(--border)", background: "var(--surface-2)" }}>
        <span style={{ fontSize: 16 }}>⏳</span>
        <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          <b style={{ color: "var(--text-dim)" }}>No best pick right now.</b> No candidate clears every gate yet — see the why-not column below.
          Live strikes/premiums/OI arrive when the US market opens; the best CSP will be crowned here automatically.
        </div>
      </div>
    );
  }
  const c = REGIME_TONE[best.regime || ""] || TONE.ok;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "12px 16px", marginBottom: 12, borderRadius: 10, border: `1px solid ${TONE.ok}66`, background: `${TONE.ok}14`, flexWrap: "wrap" }}>
      <div style={{ fontSize: 22 }}>⭐</div>
      <div style={{ flex: 1, minWidth: 220 }}>
        <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em", color: TONE.ok, fontWeight: 700 }}>Best to trade now</div>
        <div style={{ fontSize: 15, fontWeight: 700 }}>
          <span style={{ fontFamily: "var(--font-mono)" }}>{best.symbol}</span> cash-secured put
          {best.suggested_strike != null ? <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}> · ${best.suggested_strike}{best.suggested_premium != null ? ` @ $${best.suggested_premium.toFixed(2)}` : ""}</span> : null}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
          <b style={{ color: TONE.ok }}>{best.annualized_yield_pct != null ? `${best.annualized_yield_pct.toFixed(0)}%/yr` : "—"}</b> premium yield ·
          <span style={{ color: c, fontWeight: 600 }}> {best.regime}</span> regime ·
          IV-Rank {best.iv_rank != null ? `${best.iv_rank.toFixed(0)}%` : "n/a"} ·
          highest yield among {/* eligible */}the names that clear every gate
        </div>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={() => onAnalyze(best)} style={btnStyle(true, TONE.line)}>Analyze</button>
        <button disabled={busy || best.suggested_strike == null} onClick={() => onRecord(best)} style={btnStyle(best.suggested_strike != null)}>Record CSP</button>
      </div>
    </div>
  );
}

/** Market-data session banner.
 *
 * IBKR grants ONE market-data session per account. Opening the IBKR portal or
 * TWS — or another client on the same account, including an MCP connector —
 * silently takes it: auth stays valid, contracts still resolve, prices go dark
 * and the board falls back to carried premiums. That cost a full trading day of
 * investigation before anything on screen said so (18 Aug 2026).
 *
 * The owner NEEDS the portal, so the answer is not "don't use it" — it is to
 * make the state visible and give a one-click recheck for after they close it.
 */
function MarketDataBanner() {
  const [state, setState] = useState<{
    live: boolean; last: string | null; availability: string | null;
    // Provenance from the server: WHEN the quote was taken and WHERE it came
    // from, so a number on screen can be labelled rather than presented as
    // timelessly current. `reason` carries the server's plain-English cause
    // when dark.
    asOf?: string | null; source?: string | null; reason?: string | null;
    state?: string; previousClose?: string | null;
  } | null>(null);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Whether TradePro has deliberately stood down from the IBKR session.
  // The pause/resume controls already existed on the Harvest screen — but that
  // is not where anyone LEARNS the data is dark. You find out here, so the
  // action belongs here too; a control on a different screen from its symptom
  // is a control people do not find when they need it.
  const [paused, setPaused] = useState<boolean | null>(null);
  const [pausing, setPausing] = useState(false);

  const refreshPaused = useCallback(() => {
    api.ibkrHarvesterStatus()
      .then((h) => setPaused(!!(h as { paused?: boolean })?.paused))
      .catch(() => setPaused(null));
  }, []);

  const togglePause = useCallback(async () => {
    setPausing(true);
    try {
      if (paused) await api.ibkrResume();
      else await api.ibkrPause("portal login");
      refreshPaused();
    } catch (e) { setErr(String((e as Error)?.message || e)); }
    finally { setPausing(false); }
  }, [paused, refreshPaused]);

  const check = useCallback(() => {
    setChecking(true);
    refreshPaused();
    api.ibkrMarketDataCheck("SPY")
      .then((r) => { setState(r); setErr(null); })
      .catch((e) => setErr(String((e as Error)?.message || e)))
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => { check(); }, [check]);

  if (!state && !err) return null;
  const live = state?.live === true;

  // The market being CLOSED is not a fault, and must not wear the fault
  // styling. Before this, every pre-open load showed the red "market data is
  // DARK — close the IBKR portal" banner when IBKR was answering perfectly and
  // the exchange was simply shut. A red alarm that is wrong every morning is
  // how people learn to ignore the one that is right.
  if (paused) {
    return (
      <div style={{
        border: `1px solid ${TONE.warn}`, background: `${TONE.warn}14`, borderRadius: 8,
        padding: "10px 12px", marginBottom: 12, fontSize: 12, color: "var(--text)",
      }}>
        <div style={{ fontWeight: 700, color: TONE.warn, marginBottom: 4 }}>
          ⏸ TradePro is PAUSED — the IBKR session is yours
        </div>
        <div style={{ color: "var(--text-dim)", lineHeight: 1.5 }}>
          All IBKR calls are stopped so you can use the portal. Bars are not being
          harvested while this is on, and the fallback will quietly fill gaps from
          Yahoo — so resume when you are done rather than leaving it.
        </div>
        <button onClick={togglePause} disabled={pausing}
          style={{ ...btnStyle(!pausing, TONE.ok), marginTop: 8 }}>
          {pausing ? "…" : "Resume TradePro"}
        </button>
      </div>
    );
  }

  if (!live && state?.state === "closed") {
    return (
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 8 }}>
        ○ Market closed — no live print
        {state?.previousClose ? `. Previous close ${state.previousClose}` : ""}
        {state?.asOf ? ` · checked ${new Date(state.asOf).toLocaleTimeString()}` : ""}
      </div>
    );
  }

  if (live) {
    return (
      <div style={{ fontSize: 11, color: TONE.ok, marginBottom: 8 }}>
        ● IBKR market data LIVE{state?.last ? ` — SPY ${state.last}` : ""}
        {state?.availability ? ` (${state.availability})` : ""}
        {/* A live price is only meaningful with the moment it was taken —
            without it, a quote fetched before you stepped away reads exactly
            like one fetched now. */}
        {state?.asOf ? ` · as of ${new Date(state.asOf).toLocaleTimeString()}` : ""}
      </div>
    );
  }
  return (
    <div style={{
      border: `1px solid ${TONE.bad}`, background: `${TONE.bad}14`, borderRadius: 8,
      padding: "10px 12px", marginBottom: 12, fontSize: 12, color: "var(--text)",
    }}>
      <div style={{ fontWeight: 700, color: TONE.bad, marginBottom: 4 }}>
        ⛔ IBKR market data is DARK — prices below are NOT live
      </div>
      <div style={{ color: "var(--text-dim)", lineHeight: 1.5 }}>
        Auth is fine and contracts still resolve — it is the market-data session that is
        missing. IBKR allows <b>one per account</b>, and the usual cause is the
        <b> IBKR portal or TWS being open</b> (or another client on the same account).
        Close it, then press Recheck. Until then every premium on this board is a
        <b> carried</b> quote from an earlier moment.
        {err ? <> <span style={{ color: TONE.bad }}>Probe error: {err}</span></> : null}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <button onClick={check} disabled={checking}
          style={btnStyle(!checking, TONE.ok)}>
          {checking ? "Checking…" : "Recheck market data"}
        </button>
        {/* IBKR grants ONE market-data session per account. If the portal has
            it, the honest fix is to stand down rather than compete — an
            auto-recompete loop would evict the owner from their own portal
            every time a batch job ran. */}
        <button onClick={togglePause} disabled={pausing}
          style={btnStyle(!pausing, paused ? TONE.ok : TONE.warn)}
          title={paused
            ? "Take the IBKR market-data session back for TradePro."
            : "Release the IBKR session so you can use the portal. TradePro stops all IBKR calls until you resume."}>
          {pausing ? "…" : paused ? "Resume TradePro (take session back)" : "Pause TradePro (free the portal)"}
        </button>
      </div>
    </div>
  );
}

function RegimePill({ regime }: { regime: string }) {
  const c = REGIME_TONE[regime] || TONE.dim;
  return (
    <span style={{ display: "inline-block", fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", color: c, background: `${c}1f`, border: `1px solid ${c}55`, borderRadius: 999, padding: "2px 9px" }}>
      {regime}
    </span>
  );
}

// Uniform provenance, per row. The owner's standing complaint (15 Aug 2026):
// "we shouldn't hit issues where we don't know if data is coming from cache,
// yahoo or ibkr." One dot for the row's WORST input, and the full per-input
// ledger — source, as-of, age — on hover. `golden` is deliberately quiet:
// what needs to catch the eye is a fallback, a carried number, or a hole.
const PROV_TONE: Record<ProvTrust, string> = {
  golden: TONE.ok,
  derived: TONE.line,
  vendor: "var(--text-dim)",
  fallback: TONE.warn,
  carried: TONE.warn,
  unavailable: TONE.bad,
};
const PROV_WORD: Record<ProvTrust, string> = {
  golden: "IBKR",
  derived: "computed",
  vendor: "vendor",
  fallback: "FALLBACK",
  carried: "CARRIED",
  unavailable: "MISSING",
};

function ProvenanceCell({ prov }: { prov: Candidate["provenance"] }) {
  if (!prov) {
    return (
      <span style={{ color: TONE.bad, fontSize: 11 }}
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
  const tone = PROV_TONE[prov.worst] ?? TONE.bad;
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

// Compact labels for the Data pill — the full names are in the hover ledger.
const SHORT_INPUT: Record<string, string> = {
  bars: "bars", spot: "spot", premium: "premium", iv: "IV",
  open_interest: "OI", div_yield: "div", earnings: "earnings",
};

function IvGauge({ rank }: { rank: number | null }) {
  if (rank == null) return <span style={{ color: TONE.bad, fontSize: 11 }}>n/a</span>;
  const pass = rank >= 30;
  const c = pass ? TONE.ok : TONE.warn;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 96 }} title={`IV-Rank ${rank.toFixed(0)}% — ${pass ? "premium rich (≥30 gate)" : "below the 30 gate, too cheap"}`}>
      <div style={{ position: "relative", flex: 1, height: 6, borderRadius: 3, background: "var(--surface)", border: "1px solid var(--border)", overflow: "hidden" }}>
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.min(100, Math.max(0, rank))}%`, background: c }} />
        {/* 30% threshold marker */}
        <div style={{ position: "absolute", left: "30%", top: -1, bottom: -1, width: 1, background: "var(--text-muted)" }} />
      </div>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600, color: c, width: 30, textAlign: "right" }}>{rank.toFixed(0)}%</span>
    </div>
  );
}

function Flag({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 999,
      color: tone, border: `1px solid ${tone}`, whiteSpace: "nowrap",
    }}>
      {children}
    </span>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone: keyof typeof TONE }) {
  return (
    <div style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 14px", minWidth: 90 }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)", color: TONE[tone] }}>{value}</div>
    </div>
  );
}
