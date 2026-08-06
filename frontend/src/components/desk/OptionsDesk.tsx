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
  iv_rank: number | null;         // 0-100
  iv: number | null;              // fraction
  open_interest: number | null;
  spread_usd: number | null;
  eligible: boolean;              // passes the risk engine for a CSP
  blocks: string[];               // why-not (fail-visible)
  warnings: string[];
  suggested_strike: number | null;
  suggested_delta: number | null;
  suggested_premium: number | null;
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
}
interface ScreenResp {
  generated_at_utc: string | null;
  market_open: boolean;
  candidates: Candidate[];
  best_symbol?: string | null;
  eligible_count?: number;
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

  const analyze = useCallback((c: Candidate) => {
    setSeed({
      symbol: c.symbol,
      structure: "CASH_SECURED_PUT",
      strike: c.suggested_strike,
      premium: c.suggested_premium,
      contracts: 1,
      dte: 35,
    });
    document.getElementById("options-payoff")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  const rawCands = data?.candidates ?? [];
  // Eligible first, ranked by annualised yield; the rest keep screen order.
  const cands = [...rawCands].sort((a, b) => {
    if (a.eligible !== b.eligible) return a.eligible ? -1 : 1;
    if (a.eligible && b.eligible) return (b.annualized_yield_pct ?? 0) - (a.annualized_yield_pct ?? 0);
    return 0;
  });
  const eligible = cands.filter((c) => c.eligible);
  const best = cands.find((c) => c.is_best) ?? null;
  const open = positions.filter((p) => p.state !== "CLOSED");
  const realised = positions.reduce((s, p) => s + (p.realised_pnl_gbp ?? 0), 0);

  return (
    <div style={{ padding: "8px 4px" }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>Options Desk — the Wheel</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5, marginTop: 2 }}>
          Cash-secured puts on quality names, risk-first. A candidate is <b>eligible</b> only when it
          clears <b>every</b> gate: constructive regime (in/above the Ichimoku cloud, not a falling knife),
          <b> IV-Rank &gt; 30</b> (premium rich), delta 0.20–0.35, 25–50 DTE, OI &gt; 1,000, spread ≤ $0.10,
          no earnings in the window, and within capital limits. Anything we can't verify shows
          <b> BLOCKED with the reason</b> — never a silent green light.
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

      {/* ── Candidate screen ───────────────────────────────────── */}
      <SectionTitle>Candidate screen</SectionTitle>
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
                {["Symbol", "Regime", "IV-Rank", "OI / Spread", "Eligible (CSP)", "Annual yield", "Suggested", "Put vs buy now", "Size fit", "Why / why-not", ""].map((h) => (
                  <th key={h} style={{ padding: "8px 10px", fontWeight: 600, color: "var(--text-dim)", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cands.map((c) => (
                <tr key={c.symbol} style={{ borderTop: "1px solid #141b2b", background: c.is_best ? `${TONE.ok}12` : undefined }}>
                  <td style={{ padding: "8px 10px", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                    {c.is_best && <span title="best eligible CSP right now" style={{ marginRight: 5 }}>⭐</span>}{c.symbol}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    {c.regime ? <RegimePill regime={c.regime} /> : <span style={{ color: TONE.bad, fontSize: 11 }}>n/a</span>}
                  </td>
                  <td style={{ padding: "8px 10px" }}>
                    <IvGauge rank={c.iv_rank} />
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
                    {c.suggested_strike != null
                      ? `$${c.suggested_strike} · Δ${(c.suggested_delta ?? 0).toFixed(2)}${c.suggested_premium != null ? ` · $${c.suggested_premium.toFixed(2)}` : ""}`
                      : "—"}
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
                    ) : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}
                      title="Contract notional as a share of account NAV — informational only, never a hard gate here (the risk engine's own notional cap does that)">
                    {c.size_fit_pct != null ? `${c.size_fit_pct.toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ padding: "8px 10px", color: c.eligible ? TONE.warn : TONE.bad, maxWidth: 320 }}>
                    {(c.blocks?.length ? c.blocks : c.warnings)?.join("; ") || (c.eligible ? "all gates pass" : "—")}
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

      {/* ── Payoff explorer ────────────────────────────────────── */}
      <div id="options-payoff" style={{ marginBottom: 18 }}>
        <SectionTitle>Payoff explorer — max gain / loss / breakeven · place a paper trade</SectionTitle>
        <OptionsPayoff seed={seed} onPlace={placeFromExplorer} placing={busy} />
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

function RegimePill({ regime }: { regime: string }) {
  const c = REGIME_TONE[regime] || TONE.dim;
  return (
    <span style={{ display: "inline-block", fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", color: c, background: `${c}1f`, border: `1px solid ${c}55`, borderRadius: 999, padding: "2px 9px" }}>
      {regime}
    </span>
  );
}

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
