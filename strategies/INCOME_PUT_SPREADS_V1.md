# TradePro — Options Income Engine (Defined-Risk Credit Spreads)

Version: 1.0 · Date: 23 Nov 2026
Status: Spec for build. Rules below were agreed and settled with the owner on 23 Nov 2026; treat them as configuration, not suggestions.
Reference example (golden replay): ABBV Jan15'27 270/260 put credit spread on the 23 Nov 2026 quotes in §10.

> Provenance: written by the owner's other agent and handed over on 23 Nov 2026;
> committed (personal account details removed — the repo is public) as the pre-registration for `INCOME_PUT_SPREADS_V1`. The
> TradePro implementation is `tradepro_strategies/income_spreads.py` (config
> in settings-kv `income_engine`, defaults = §2). Stage G (the IBKR order
> instruction) is the other agent's: TradePro never creates or submits orders
> on the live account — it publishes the candidates and the monitor.

## 1. Purpose

Replace a legacy naked short-put book with a steady-income engine of defined-risk credit spreads spread across sectors and indices.

The engine must do, automatically, what a trader does by hand for a trade like the §10 example:

- Pick candidates from a diversified, non-tech universe
- Apply earnings, trend and technical gates
- Choose strikes, price the spread from live quotes, compute probabilities
- Size to the max-loss rule and the sector/total risk caps
- Present the top candidates and, on approval, create an IBKR order instruction (combo) for the owner to review and submit
- Monitor open spreads daily and alert on take-profit / exit rules
- Log every candidate, decision and fill

Human-in-the-loop: the engine never submits live orders. It creates IBKR order instructions (REVIEW_AND_APPROVE flow) that the owner reviews and submits.

## 2. Settled configuration (agreed rules)

All values must live in one config file/table so they can be changed without code changes.

```yaml
income_engine:
  capital_gbp: 100000            # capital allocated to the income engine
  target_annual_return: 0.10     # 10%/yr ≈ £10k/yr ≈ £830/month
  max_loss_per_trade_gbp: 3000   # hard cap per spread position (all contracts)
  max_loss_per_sector_gbp: 6000  # hard cap per GICS sector across open spreads
  phase_1_total_max_loss_gbp: 6000   # total new-spread max loss while legacy book is large
  phase_1_exit_condition: "legacy short-put notional <= 50% of 23-Nov-2026 level"
  universe_mix:
    index_share: 0.5             # QQQ, XSP, IWM (index/ETF options)
    single_name_share: 0.5       # quality, dividend-paying, non-tech
  excluded_sectors_phase_1: [semiconductors, memory_storage]
  no_two_names_same_sector: true   # within single-name bucket
  dte_min: 25
  dte_max: 60                    # prefer monthly expiries
  earnings_inside_expiry: forbidden
  require_above_200dma: true
  require_above_ichimoku_cloud: true      # warn-only for indices
  short_strike_below_30d_low: true        # short strike must sit below the 30-day closing low
  short_put_delta_range: [0.15, 0.30]     # absolute delta of the short leg
  spread_width_preference: [5, 10]        # in strike points; choose per underlying
  min_credit_to_width: 0.15               # reject if credit/width < 15%
  min_pop_full_credit: 0.70               # prob. of expiring above short strike (IV-based)
  max_leg_bid_ask_pct: 0.15               # reject if a leg's (ask-bid)/mid > 15%
  min_open_interest_short_leg: 100
  take_profit_pct_of_credit: 0.50         # buy back when spread value <= 50% of credit received
  exit_rule: "close next session if underlying CLOSES below the 30d swing low used at entry"
  avoid_names_already_held: true          # no new spread on a name with existing stock/option exposure
  fx_source: ibkr_account_balances        # use live GBP/USD from IBKR for GBP caps
```

## 3. Universe

### 3.1 Index bucket (~50% of trades)

| Symbol | Notes |
|---|---|
| QQQ | ETF options |
| XSP | Mini-SPX, cash-settled, European — verify permission on first use |
| IWM | ETF options — verify permission |

Phase 1 note: index put spreads are correlated with the legacy tech book. In Phase 1, prefer single names; allow index put spreads only if total Phase-1 risk cap has room and the index is above its 200-day.

### 3.2 Single-name bucket (~50% of trades)

Seed list (extendable via config): JPM, V, JNJ, ABBV, XOM, ACN, GOOGL, PG, KO, PEP, MRK, UNH, HD, CAT

Filters:

- Dividend payer
- Market cap > $50bn; option volume adequate (see liquidity gates)
- Not in excluded sectors (Phase 1: semis, memory/storage)
- Not currently held (stock or option) in the account → read positions via IBKR

## 4. Pipeline

### 4.1 Stage A — Eligibility gates (per underlying)

Reject and log the reason if any gate fails.

| Gate | Rule | Data |
|---|---|---|
| Earnings | Next earnings date must be after the candidate expiry | Earnings calendar (existing TradePro get_earnings_calendar) |
| Trend | Last close > 200-day SMA | Daily bars |
| Cloud | Price above Ichimoku cloud (hard for single names, warn for indices) | Daily bars |
| Falling knife | Reject if 10-day return < −8% and price < 200-day (QCOM/ORCL lesson) | Daily bars |
| Already held | No existing exposure to the name | IBKR positions |
| Sector cap | Sector open max-loss + this trade ≤ £6k | Engine ledger |
| Total cap | Phase-1 total open max-loss + this trade ≤ £6k | Engine ledger |

### 4.2 Stage B — Expiry & strike selection

- Choose the monthly expiry with DTE in [25, 60]; if two qualify, prefer the nearer one.
- Compute swing_low_30d = minimum daily close of the last 30 sessions.
- Short strike = highest listed strike that is ≤ swing_low_30d and has |delta| in [0.15, 0.30].
- Long strike = short strike − width (width from config; use the nearest listed strike).
- If no strike satisfies both rules, try the next width; else reject.

### 4.3 Stage C — Live pricing

- Pull live bid/ask for both legs from IBKR (never use stale or carried quotes; if a quote is missing or older than 60s, reject the candidate with reason stale_quote).
- mid_short = (bid+ask)/2, mid_long = (bid+ask)/2
- credit_mid = mid_short − mid_long
- credit_natural = bid_short − ask_long (worst-case fill)
- Liquidity gates: each leg (ask−bid)/mid ≤ 15%; short-leg OI ≥ 100.
- Reject if credit_mid / width < 0.15.

### 4.4 Stage D — Probabilities (lognormal, risk-neutral, zero drift)

Inputs: spot S, short strike K1, long strike K2, credit C, time T = DTE/365, volatility σ.

```
N(x) = standard normal CDF
s     = σ · sqrt(T)
BE    = K1 − C
P_full_profit = 1 − N( ln(K1/S) / s )     # expires above short strike
P_loss_any    = N( ln(BE/S) / s )          # below breakeven
P_max_loss    = N( ln(K2/S) / s )          # below long strike
one_sd_move_pct = s
```

Compute twice: with σ = implied vol of the short leg and with σ = 30-day realised vol. Report both.

Edge flag: iv_hv_ratio = IV_short / HV_30d. Show it; prefer candidates with ratio ≥ 1.05 (seller is paid more than recent movement).

Reject if P_full_profit (IV-based) < 0.70.

### 4.5 Stage E — Sizing

```
fx                = GBP per USD (live, from IBKR balances)
max_loss_per_spread_usd = (width − C) × 100
cap_usd           = min(max_loss_per_trade_gbp, sector_room_gbp, total_room_gbp) / fx
contracts         = floor( cap_usd / max_loss_per_spread_usd )
reject if contracts == 0
income_usd        = C × 100 × contracts
max_loss_usd      = max_loss_per_spread_usd × contracts
```

### 4.6 Stage F — Ranking & output

Score (higher is better; weights configurable):

```
score = 0.35·P_full_profit(IV)
      + 0.25·min(credit/width / 0.30, 1)
      + 0.20·min(iv_hv_ratio / 1.3, 1)
      + 0.10·(cushion_pct / one_sd_move_pct)   # cushion_pct = (S − K1)/S
      + 0.10·liquidity_score                   # 1 − avg leg spread%
```

Output the top 3 candidates that fit the caps, plus the rejected list with reasons.

### 4.7 Stage G — Order instruction (on approval only)

On the owner's approval of a candidate:

- Build combo with IBKR get_combo_identifier: legs [{short_put: +1}, {long_put: −1}] — i.e. the bear put definition.
- Create instruction with create_order_instruction(side=SELL, quantity=contracts, order_type=LIMIT, limit_price=credit_mid rounded to 0.05 toward the bid, tif=DAY).
- Important: SELL on the bear-put combo = short the higher strike, long the lower strike = credit. IBKR labels this "Bull Put" in the order list — expected.
- Return the IBKR deep-link URL for the owner to review/submit.
- Price-walk guidance in the message: start at mid, step down 0.05 every ~5 min, floor = max(credit_natural, 0.85 × credit_mid).

### 4.8 Stage H — Daily monitor (open spreads)

Run once after US close (and optionally mid-session):

| Check | Action |
|---|---|
| Spread mark ≤ 50% of entry credit | Alert TAKE PROFIT with buy-back limit = current mid |
| Underlying closed below entry swing_low_30d | Alert EXIT NEXT SESSION with current debit to close |
| Earnings date moved inside expiry | Alert REVIEW |
| DTE ≤ 7 and short strike within 2% of spot | Alert GAMMA RISK — consider closing |
| Any short leg assigned early | Alert ASSIGNED (American-style options) |

Alerts go to the owner (existing TradePro notification path).

## 5. Data & tool dependencies

| Need | Source | Status / known issue |
|---|---|---|
| Positions, balances, FX | IBKR connector (get_account_positions, get_account_balances) | Works |
| Live option quotes, greeks, IV | IBKR get_price_snapshot on option conids | Works; greeks sometimes empty → fall back to IV field + own Black-Scholes |
| Option chain structure | IBKR get_option_parameters + get_option_data | Works (intermittent 503s → retry with backoff ×3) |
| TradePro get_option_chain | TradePro | Bug: fails with "could not read spot after 5 primed attempts" — stream never warms. Fix or route to IBKR direct calls |
| Daily bars / 200-DMA / cloud / RSI / BB | TradePro get_market_state | Works (as_of is prior close — fine for gates) |
| Earnings dates | TradePro get_earnings_calendar | Verify coverage for all universe names |
| News on candidates | TradePro get_news_with_sentiment | Bug: returned 0 items for NVDA on a −7% day → feed broken. Wire IBKR TWS API news (reqNewsProviders, reqHistoricalNews, reqNewsArticle) |
| IBKR FYIs | TradePro get_ibkr_fyi | Bug: requests >10 items (HTTP 400 "max num > 10") and points at the paper account |
| Order instructions | IBKR get_combo_identifier, create_order_instruction | Works (REVIEW_AND_APPROVE flow) |

## 6. Interfaces (MCP tools to add to TradePro)

```
income_scan(mode: "daily" | "on_demand", max_candidates: int = 3)
  -> { candidates: [Candidate], rejected: [{symbol, reason}], caps: CapState, as_of }

income_create_instruction(candidate_id: str, contracts: int | null, limit: float | null)
  -> { instruction_id, ibkr_url, summary }

income_monitor()
  -> { open_spreads: [OpenSpread], alerts: [Alert] }

income_ledger(period: str)
  -> { trades: [...], realised_pnl, open_max_loss_gbp, income_ytd, vs_target }
```

Candidate schema

```json
{
  "id": "ABBV-20270115-270-260",
  "symbol": "ABBV", "sector": "Health Care", "bucket": "single",
  "spot": 289.33, "expiry": "2027-01-15", "dte": 53,
  "short_strike": 270, "long_strike": 260, "width": 10,
  "short_bid": 4.10, "short_ask": 4.55, "long_bid": 2.20, "long_ask": 2.75,
  "credit_mid": 1.85, "credit_natural": 1.35, "credit_to_width": 0.185,
  "breakeven": 268.15, "cushion_pct": 0.067,
  "iv_short": 0.278, "hv_30d": 0.231, "iv_hv_ratio": 1.20,
  "p_full_profit_iv": 0.73, "p_full_profit_hv": 0.77,
  "p_max_loss_iv": 0.17, "p_max_loss_hv": 0.12,
  "contracts": 4, "income_usd": 740, "max_loss_usd": 3260, "max_loss_gbp": 2490,
  "gates": { "earnings_ok": true, "next_earnings": "2027-02-03",
             "above_200dma": true, "sma200": 242.44,
             "above_cloud": true, "swing_low_30d": 274.39,
             "rsi14": 49.2, "bb_pct_b": 0.23 },
  "score": 0.78,
  "exit_rule": "close next session if ABBV closes < 274.39",
  "take_profit_at": 0.93
}
```

## 7. Ledger & logging

Persist every:

- Scan: timestamp, all candidates (incl. rejected + reason), quotes used
- Instruction: id, candidate snapshot, limit
- Fill: per-leg prices (IBKR returns per-leg fills, possibly across several venues), net credit, commission
- Exit: reason (take-profit / swing-low exit / expiry / assignment), debit, realised P&L

Monthly report: income vs £830/month target, win rate, avg win/loss, open risk by sector.

## 8. Phasing

| Phase | Scope |
|---|---|
| 1 (build now) | Single-name put credit spreads only; gates A–F; scheduled scans (13:45 + 16:30) and on-demand; instruction creation; daily monitor (21:15); ledger. Total open max-loss ≤ £6k |
| 2 | Add index put spreads (QQQ/XSP/IWM) once legacy short-put notional ≤ 50% of 23-Nov level; raise total cap toward full £100k engine (~6–8 concurrent spreads) |
| 3 | Call credit spreads / iron condors when IV percentile is high and trend is flat |
| 4 | Backtest harness using TradePro's captured option quotes (capture began 13 Aug 2026); pre-register acceptance gates before running |

Scheduling (settled with the owner, 23 Nov 2026): the scan runs on a schedule. on_demand mode stays available for ad-hoc runs.

| Job | When (London time, US trading days) | Output |
|---|---|---|
| income_scan(mode="daily") | 13:45 (45 min before US open) | Top 3 candidates priced on pre-market spot; re-price legs live at 14:40 before any instruction is created |
| income_scan re-check | 16:30 (mid-session) | Only if a 13:45 candidate was not acted on, or a new name passed the gates |
| income_monitor() | 21:15 (after US close) | Take-profit / exit / gamma / assignment alerts on open spreads |

Rules:

- Skip US market holidays and half-days (use exchange calendar).
- No notification when there is nothing actionable (no candidates passing gates and no alerts).
- A scan never creates an order instruction by itself — the owner approves a candidate first.

## 9. Acceptance tests

- Golden replay (ABBV, 23 Nov 2026 quotes): with the quotes in §10 and the §2 caps, the engine must output ABBV Jan15'27 270/260, credit_mid ≈ 1.85, contracts = 4, max_loss ≈ $3,260, P_full_profit(IV) ≈ 0.73 (±0.02), exit level 274.39.
- Earnings gate: ACN on 23 Nov with Jan15 expiry → rejected (earnings_inside_expiry, ACN reports 17 Dec 2026).
- Sector cap: with one Health Care spread open (£2.49k), a JNJ candidate is sized so the sector total ≤ £6k; if not possible → reject sector_cap.
- Falling knife: a name with 10-day return −17% and below 200-DMA (e.g. CRM on 20 Nov 2026) → rejected.
- Already held: a name with stock or option exposure in the account → rejected already_held.
- Stale quote: if any leg quote missing/stale → rejected, no instruction created.
- Combo direction: instruction created for a credit spread must show IBKR "Credit" on review; a unit test asserts SELL side on the (+short_strike, −long_strike) combo.
- Monitor: if ABBV closes < 274.39 → EXIT NEXT SESSION alert generated once (no duplicate alerts).

## 10. Reference data — ABBV worked example, 23 Nov 2026 quotes

| Item | Value |
|---|---|
| Spot | 289.33 (+1.1% on day) |
| 200-DMA / cloud | 242.44 / price above |
| RSI14 / BB %B | 49.2 / 0.23 |
| 30-day closing low | 274.39 |
| 52-wk high / 13-wk low | 302.91 / 247.00 |
| ATR14 | 2.3% |
| Next earnings | 3 Feb 2027 |
| Expiry / DTE | 15 Jan 2027 / 53 |
| 270P quote at decision | 4.10 / 4.55 |
| 260P quote at decision | 2.20 / 2.75 |
| IV (270P) / HV30 | 27.8% / 23.1% |
| Sized trade | 4 spreads at the §2 caps, net 1.85 credit at mid |
| Income / max loss | $740 / $3,260 |
| P(full profit) IV / HV | ~73% / ~77% |
| P(max loss) IV / HV | ~17% / ~12% |
| Take profit | buy back ≤ 0.93 |
| Exit | ABBV close < 274.39 → close next session |

Context that made it a good candidate: ABBV fell only ~4% in the week the Nasdaq fell ~10% (defensive, low beta, uncorrelated with a tech-heavy book).

## 11. Amendment (owner, 23 Nov 2026) — the cash-secured put beside every spread

The long leg buys the loss cap and costs most of the credit. In some cases,
cash permitting, the owner would rather sell the put cash-secured. The engine
therefore prices BOTH structures for every candidate that passes the gates:

| | put credit spread (§4) | cash-secured put |
|---|---|---|
| legs | short K1, long K2 | short K1 only |
| credit | mid_short − mid_long | mid_short |
| risk | (width − credit) × 100 per spread | strike to zero; cash K1 × 100 per contract |
| sizing | §4.5 loss caps (£3k / £6k / £6k) | CASH caps: `csp_max_notional_per_name_gbp` (25,000), `csp_total_notional_gbp` (50,000) |
| probability floor | `min_pop_full_credit` 0.70 | `csp_min_pop_full_credit` 0.75 (the downside is open) |
| switch | — | `csp_enabled` (true) |

Shown side by side with income, cash required, income as % of cash and
annualised, breakeven, P(full profit) and the assignment risk. The owner picks
per trade; the ledger records `structure`. Phase-1 caution stands: the legacy
drawdown came from puts sold into a sell-off, so the CSP is offered only on
names that already pass every §4.1 gate.
