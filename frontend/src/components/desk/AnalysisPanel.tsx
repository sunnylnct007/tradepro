/**
 * The numbers a premium seller actually decides on, for ONE candidate.
 *
 * Owner, 1 Sep 2026: "we need more analysis paraneters on analysis screen. it
 * doenst display as pro".
 *
 * WHAT WAS WRONG. "Analyze" opened a payoff chart seeded with seven fields —
 * symbol, structure, strike, premium, spot, contracts, dte — and discarded
 * everything else the row carried: IV, IV/HV, open interest, spread, the
 * forward, the model cross-check, provenance, the decision trace. The chart
 * showed the shape of the trade and none of its quality.
 *
 * The screen also never carried THETA, which for a seller is the headline
 * number: it is the daily income rate, the entire reason the position exists.
 * A "0.27 delta put paying $2.37" says nothing about whether you are being paid
 * enough per day to carry the assignment risk.
 *
 * WHAT THIS SHOWS, grouped the way the decision is actually made:
 *   THE CONTRACT   what to place — expiry, tenor, strike, DTE
 *   WHAT YOU EARN  premium, per-day theta, yield, annualised
 *   WHAT YOU RISK  break-even, distance to it, assignment odds, collateral
 *   THE PRICE      bid/ask, spread as % of mid, model vs mid
 *   THE VOL        IV, IV/HV, IV-Rank, vega
 *   FILLABILITY    open interest and spread, with their provenance
 *
 * EVERY NUMBER SAYS WHERE IT CAME FROM. Greeks other than delta are DERIVED
 * from the row's own IV via Black-Scholes, not fetched — labelled "model", so
 * nobody reads a computed theta as a broker quote. A missing input renders "—",
 * never 0: a zero theta reads as "no decay", which is a claim about the trade.
 */

const OK = "#0ca30c";
const WARN = "#fab219";
const BAD = "#ec835a";
const MUTED = "var(--text-muted)";

export interface AnalysisRow {
  symbol: string;
  regime?: string | null;
  expiry?: string | null;
  expiry_kind?: string | null;
  dte?: number | null;
  suggested_strike?: number | null;
  suggested_delta?: number | null;
  suggested_premium?: number | null;
  bid?: number | null;
  ask?: number | null;
  spread_usd?: number | null;
  spread_pct_of_mid?: number | null;
  open_interest?: number | null;
  open_interest_source?: string | null;
  iv?: number | null;
  iv_hv_ratio?: number | null;
  iv_rank?: number | null;
  iv_rank_days?: number | null;
  theta_per_day?: number | null;
  vega_per_1pct?: number | null;
  gamma?: number | null;
  model_delta?: number | null;
  greeks_basis?: string | null;
  model_price?: number | null;
  model_vs_mid_pct?: number | null;
  ref_close?: number | null;
  forward_price?: number | null;
  annualized_yield_pct?: number | null;
  size_fit_pct?: number | null;
  eligible?: boolean;
  blocks?: string[];
  warnings?: string[];
}

function Cell({ label, value, sub, tone, help }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode;
  tone?: string; help?: string;
}) {
  return (
    <div title={help}
         style={{ padding: "8px 10px", border: "1px solid var(--border)",
                  borderRadius: 6, background: "var(--surface-2)",
                  cursor: help ? "help" : undefined, minWidth: 0 }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em",
                    color: MUTED, whiteSpace: "nowrap" }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2,
                    fontVariantNumeric: "tabular-nums", color: tone ?? "var(--text)" }}>
        {value}
      </div>
      {sub != null && (
        <div style={{ fontSize: 10.5, color: MUTED, marginTop: 1 }}>{sub}</div>
      )}
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em",
                    color: MUTED, marginBottom: 6 }}>{title}</div>
      <div style={{ display: "grid", gap: 8,
                    gridTemplateColumns: "repeat(auto-fit, minmax(132px, 1fr))" }}>
        {children}
      </div>
    </div>
  );
}

const dash = <span style={{ color: MUTED }}>—</span>;
const n = (v: number | null | undefined, d = 2, suf = "") =>
  v === null || v === undefined || Number.isNaN(v) ? dash : `${v.toFixed(d)}${suf}`;

export function AnalysisPanel({ r }: { r: AnalysisRow }) {
  const strike = r.suggested_strike ?? null;
  const prem = r.suggested_premium ?? null;
  const spot = r.ref_close ?? null;

  // One contract = 100 shares. Collateral for a cash-secured put is the full
  // strike: assignment obligates you to buy 100 shares AT the strike.
  const collateral = strike != null ? strike * 100 : null;
  const premiumUsd = prem != null ? prem * 100 : null;
  const breakeven = strike != null && prem != null ? strike - prem : null;
  // Distance to break-even is the honest "how wrong can I be" number — further
  // than the strike, because the premium is a buffer.
  const beCushionPct = breakeven != null && spot ? (spot - breakeven) / spot * 100 : null;
  const yieldPct = premiumUsd != null && collateral ? premiumUsd / collateral * 100 : null;
  // Theta is NEGATIVE for a long option; the seller earns it. Show the earning.
  const thetaDayUsd = r.theta_per_day != null ? Math.abs(r.theta_per_day) * 100 : null;
  // What fraction of the whole premium arrives per day at the current rate.
  const thetaPctOfPrem = thetaDayUsd != null && premiumUsd ? thetaDayUsd / premiumUsd * 100 : null;
  const assignPct = r.suggested_delta != null ? Math.abs(r.suggested_delta) * 100 : null;
  // 1-sigma move over the remaining life, from the contract's own IV.
  const expMovePct = r.iv != null && r.dte
    ? r.iv * Math.sqrt(Math.max(r.dte, 1) / 365) * 100 : null;
  const spreadPct = r.spread_pct_of_mid != null
    ? r.spread_pct_of_mid * (r.spread_pct_of_mid <= 1 ? 100 : 1) : null;
  const oiTrusted = ["ibkr", "ibkr_web", "g3"].includes(String(r.open_interest_source));

  const expShown = r.expiry
    ? (() => { const e = String(r.expiry).replace(/-/g, "");
               return `${e.slice(0, 4)}-${e.slice(4, 6)}-${e.slice(6, 8)}`; })()
    : null;

  return (
    <div style={{ fontSize: 12.5 }}>
      <Group title="The contract — what to place">
        <Cell label="Symbol" value={r.symbol} sub={r.regime ?? undefined} />
        <Cell label="Expiry" value={expShown ?? dash}
              sub={r.expiry_kind === "monthly" ? "standard monthly" : r.expiry_kind ? "weekly" : undefined}
              help="Standard monthlies (3rd Friday) hold the deepest open interest and the tightest spreads. Weeklies decay faster and are thinner." />
        <Cell label="Strike" value={n(strike, 2)}
              sub={spot ? `${((strike! - spot) / spot * 100).toFixed(1)}% vs spot` : undefined} />
        <Cell label="DTE" value={r.dte ?? dash} sub="calendar days" />
      </Group>

      <Group title="What you earn">
        <Cell label="Premium" value={premiumUsd != null ? `$${premiumUsd.toFixed(0)}` : dash}
              sub={prem != null ? `${prem.toFixed(2)} / share` : undefined} tone={OK} />
        <Cell label="Theta / day" value={thetaDayUsd != null ? `$${thetaDayUsd.toFixed(2)}` : dash}
              sub={thetaPctOfPrem != null ? `${thetaPctOfPrem.toFixed(1)}% of premium/day · model` : "model"}
              tone={thetaDayUsd != null ? OK : undefined}
              help="What one contract earns per calendar day at the current rate — the reason a short-premium position exists. DERIVED from this row's IV via Black-Scholes, not a broker quote. Decay accelerates as expiry nears." />
        <Cell label="Yield" value={n(yieldPct, 2, "%")} sub="on collateral" />
        <Cell label="Annualised" value={n(r.annualized_yield_pct, 1, "%")}
              tone={(r.annualized_yield_pct ?? 0) >= 15 ? OK : undefined}
              help="Yield scaled to a year at this rate. It assumes you keep re-selling at the same terms, which is an assumption, not a forecast." />
      </Group>

      <Group title="What you risk">
        <Cell label="Break-even" value={n(breakeven, 2)}
              sub={beCushionPct != null ? `${beCushionPct.toFixed(1)}% below spot` : undefined}
              help="Strike minus premium. Below this you are losing money at expiry — it sits further out than the strike because the premium is a buffer." />
        <Cell label="Assignment" value={assignPct != null ? `${assignPct.toFixed(0)}%` : dash}
              sub="|delta| as probability"
              tone={assignPct != null && assignPct > 35 ? WARN : undefined}
              help="The market's own estimate of finishing in-the-money. |delta| is the standard proxy. If IV/HV is below 1 the QUOTED delta understates this — see the row's warning." />
        <Cell label="Collateral" value={collateral != null ? `$${collateral.toLocaleString()}` : dash}
              sub={r.size_fit_pct != null ? `${r.size_fit_pct.toFixed(1)}% of NAV` : "cash secured"} />
        <Cell label="Max loss" value={collateral != null && premiumUsd != null
                ? `$${(collateral - premiumUsd).toLocaleString()}` : dash}
              sub="if it goes to zero" tone={BAD}
              help="The true worst case for a cash-secured put: the stock goes to zero, you are assigned at the strike, and you keep the premium. Unlikely, but it is the real floor." />
      </Group>

      <Group title="The price you are getting">
        <Cell label="Bid / Ask" value={r.bid != null && r.ask != null
                ? `${r.bid.toFixed(2)} / ${r.ask.toFixed(2)}` : dash}
              sub={prem != null ? `mid ${prem.toFixed(2)}` : undefined} />
        <Cell label="Spread" value={n(r.spread_usd, 2)}
              sub={spreadPct != null ? `${spreadPct.toFixed(1)}% of mid` : undefined}
              tone={spreadPct != null && spreadPct > 10 ? WARN : undefined}
              help="The verified liquidity signal — it measures what actually decides your fill. Work a limit at the mid; a wide market means you will not get it." />
        <Cell label="Model" value={n(r.model_price, 2)}
              sub={r.model_vs_mid_pct != null ? `${r.model_vs_mid_pct > 0 ? "+" : ""}${r.model_vs_mid_pct.toFixed(1)}% vs mid` : "Black-Scholes"}
              help="Black-Scholes price at this row's IV. A large gap to the mid means the quote and the model disagree — usually a stale quote or an IV the model cannot reproduce." />
        <Cell label="Forward" value={n(r.forward_price, 2)}
              sub="expiry anchor"
              help="Where the market prices the underlying AT expiry — the honest anchor for 'how far OTM is this', rather than today's spot." />
      </Group>

      <Group title="Volatility — what you are being paid for">
        <Cell label="IV" value={r.iv != null ? `${(r.iv * 100).toFixed(1)}%` : dash}
              sub="this contract" />
        <Cell label="IV / HV" value={n(r.iv_hv_ratio, 2)}
              tone={(r.iv_hv_ratio ?? 1) < 1 ? WARN : OK}
              sub={(r.iv_hv_ratio ?? 1) < 1 ? "implied BELOW realised" : "implied above realised"}
              help="Above 1 means options price MORE movement than the stock is delivering — you are being paid for risk that has not shown up. Below 1 the quoted delta understates assignment risk." />
        <Cell label="IV-Rank" value={r.iv_rank != null ? `${r.iv_rank.toFixed(0)}%` : dash}
              sub={r.iv_rank == null ? `needs 60d, have ${r.iv_rank_days ?? 0}` : "of its own 1y range"}
              help="Where today's IV sits in this name's own year. Until the dataset matures the IV/HV bridge stands in — which is why this reads n/a." />
        <Cell label="Expected move" value={expMovePct != null ? `±${expMovePct.toFixed(1)}%` : dash}
              sub="1σ by expiry"
              help="One standard deviation of movement over the remaining life, from this contract's IV. Roughly two-thirds of outcomes land inside it." />
      </Group>

      <Group title="Can you actually fill it">
        <Cell label="Open interest"
              value={r.open_interest != null ? r.open_interest.toLocaleString() : dash}
              sub={oiTrusted ? "IBKR" : r.open_interest_source ? `${r.open_interest_source} — context only` : undefined}
              tone={!oiTrusted && r.open_interest != null ? WARN : undefined}
              help="Contracts outstanding at this strike. Only used to reject a candidate when it comes from IBKR; our own capture has measured an order of magnitude low and informs only." />
        <Cell label="Vega" value={n(r.vega_per_1pct, 3)} sub="per 1pt IV · model"
              help="How much the option's value moves per one percentage point of IV. As a seller, rising IV works against you before expiry even if the stock does not move." />
        <Cell label="Gamma" value={n(r.gamma, 4)} sub="model"
              help="How fast delta changes as the stock moves. High gamma near expiry is what turns a quiet short put into a fast-moving one." />
        <Cell label="Status" value={r.eligible ? "tradeable" : "blocked"}
              tone={r.eligible ? OK : MUTED}
              sub={r.blocks?.length ? `${r.blocks.length} gate${r.blocks.length > 1 ? "s" : ""}` : undefined} />
      </Group>

      {(r.warnings?.length || r.blocks?.length) ? (
        <div style={{ marginTop: 4, padding: "8px 10px", borderRadius: 6,
                      border: "1px solid var(--border)", background: "var(--surface-2)",
                      fontSize: 11.5, color: MUTED, lineHeight: 1.55 }}>
          {r.blocks?.length ? (
            <div><b style={{ color: "var(--text)" }}>Why not:</b> {r.blocks.join(" ")}</div>
          ) : null}
          {r.warnings?.length ? (
            <div style={{ marginTop: r.blocks?.length ? 6 : 0 }}>
              <b style={{ color: WARN }}>Warnings:</b> {r.warnings.join(" ")}
            </div>
          ) : null}
        </div>
      ) : null}

      <div style={{ marginTop: 8, fontSize: 10.5, color: MUTED, lineHeight: 1.5 }}>
        Delta and IV come from IBKR. Theta, vega and gamma are <b>derived</b> from
        this row's IV via Black-Scholes and are labelled <b>model</b> — reproducible
        by hand from the strike, spot, DTE and IV above, never presented as a broker
        quote. A missing input shows <b>—</b>, never a zero.
      </div>
    </div>
  );
}
