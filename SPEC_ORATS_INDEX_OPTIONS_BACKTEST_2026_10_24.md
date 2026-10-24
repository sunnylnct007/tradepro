# Spec — ORATS ingest + 0DTE index-options backtest

**Date:** 2026-10-24
**Repo state at drafting:** `6855b11`
**Status:** specification only. Nothing here goes to the live desk until its
backtest clears the pre-registered gates in §6.

**Assumption to confirm first:** the owner's new subscription is **ORATS Intraday
Data API + Live** (1,000,000 requests/month, one-minute history back to Aug 2020).
If it is only the Data API tier (EOD history from 2007), Part A (§2–§3) cannot be
built, and only Part B (§7) applies.

---

## 0. Why

On 2026-10-24 a 0DTE SPY backtest was run on 479 days of daily bars, with every
option priced by Black-Scholes. It tested five structures:

- ATM call fly
- the 23 Oct bull fly
- ATM iron fly
- 1σ iron condor
- 1σ naked strangle

It produced **no usable verdict**:

- **At 1.0× realised open-to-close vol,** every structure lost money after costs.
- **At 1.2× realised vol,** every structure made money.

So the sign of the edge depends entirely on one number the model has to assume:
**how much the market actually charges for same-day options compared with what
SPY then moves.** No amount of modelling settles that. Only real historical 0DTE
quotes can.

TradePro cannot answer it today:

- **`option_quote_daily`** (migration 063) is one snapshot a day. It has never
  captured a 0DTE expiry, the expiry it captures changes from day to day, and
  `iv` is mostly NULL.
- **IBKR serves no history for expired contracts.**

ORATS fixes both problems. It has one-minute chains back to Aug 2020, with real
bid/ask, SMV greeks and IV, for every expiry including same-day ones.

---

## 1. Scope

| In | Out |
|---|---|
| ORATS provider module plus secret | Replacing the IBKR or Yahoo chain paths used by the live screens |
| Historical one-minute ingest for SPY, QQQ, IWM, SPX (§2) | BANKNIFTY / NIFTY: ORATS is US-only, so the Indian strangle keeps its own evidence path |
| Parquet store on the bar-cache S3 pattern (§3) | Any order placement |
| Study 0: implied vs realised (§4) | UI, beyond one read-only MCP tool (§8) |
| Strategy replay engine plus grid (§5) | |
| Pre-registered gates file (§6) | |
| Part B: EOD strikes for the wheel backtest (§7) | |

---

## 2. Provider — `quant_engine/options/orats.py`

### 2.1 Secret

Add `orats-api-token` through `secrets.get_secret` (env
`TRADEPRO_ORATS_API_TOKEN`, then the `tradepro/all` bundle). Never log the token.
Every URL carries it as `?token=`, so it must be scrubbed from logged URLs and
exception messages.

### 2.2 Endpoints used (verify each with one call before building on it)

| Purpose | Endpoint | Key params |
|---|---|---|
| One-minute chain, history | `GET https://api.orats.io/datav2/historical/one-minute/strikes/chain` | `ticker`, `tradeDate=YYYYMMDDHHMM` (US/Eastern) |
| One contract, minute range | `GET …/historical/one-minute/strikes/option` | `ticker=<OPRA>` (e.g. `SPY261023C00765000`), `tradeDate=start,end`, max 40 trading days per call, from Jan 2022 only |
| EOD strikes, history (Part B) | `GET https://api.orats.io/datav2/hist/strikes` | `ticker`, `tradeDate=YYYY-MM-DD` |

Notes from the ORATS docs that the code must respect:

- Responses are **CSV**, not JSON. Parse with `pandas.read_csv(StringIO(text))`.
- **The current day is only available up to 15 minutes ago.** History ingest
  must never request today.
- **`stockPrice` and `spotPrice` are derived from options via put-call parity.**
  ORATS describes them as reference values. Use them to select strikes, never as
  the trigger price. See §5.3.
- **Row shape:** calls and puts share one row per strike, with
  `callBidPrice`/`callAskPrice`/`putBidPrice`/`putAskPrice`,
  `callMidIv`/`putMidIv`, `smvVol` and greeks, plus `dte`, `expirDate` and
  `snapShotEstTime`.

### 2.3 Client behaviour

- **Pacing:** a token bucket at a configurable rate (default 5 req/s). Also keep
  a monthly request counter in `run_log`, and refuse to start a backfill whose
  planned call count would push the month over 80% of quota.
- **On 429 / 5xx:** back off exponentially, retry at most 3 times, then mark the
  slot `failed` in the manifest (§3.2) and move on. This is the same discipline
  as `option_chain_capture.py`.
- **Provenance:** every row is written with `source='orats_1m'`. An ORATS quote
  must never be indistinguishable from a `g3_chain` or `yfinance_chain` one.

---

## 3. Historical ingest — `cli/orats_backfill.py`

### 3.1 What to pull

For each underlying in `ORATS_INDEX_SET = [SPY, QQQ, IWM, SPX]` (SPX only if the
`/tickers` check confirms coverage), on every trading day from 2020-08-03 to
yesterday:

- **Snapshot grid:** every 5 minutes from 09:35 to 15:55 ET, which is 76 calls
  per symbol-day. Make it configurable. A 1-minute grid between 09:30–10:00 and
  15:00–16:00 is a Phase 2 option.
- **Filter before storing.** The chain call returns every expiry, so keep only:
  - `dte <= 7`. This covers 0DTE, 1DTE and the weekly.
  - strikes within **±4%** of `stockPrice`.
  - all quote, IV and greek columns, with no other columns dropped.
- **Mark same-day expiries.** SPY only gained an expiry every weekday in late
  2022; before that, same-day expiries were Mon/Wed/Fri. Do not hard-code the
  date. Derive `has_0dte` per day from the data (`min(dte) == 0`) and store it
  on the day manifest.

**Budget:** 4 symbols × ~1,560 days × 76 = **~474k calls** for a full backfill.
That is about half of one month's quota. Run SPY first: ~119k calls, about 7
hours at 5 req/s. Then QQQ, then IWM and SPX.

### 3.2 Store — Parquet on S3, not Postgres

This is around 10–20M rows per symbol after filtering. Put it alongside the bar
cache, using the same `s3_mirror` write-through/read-through pattern and the same
fail-safe rule (S3 down means local only, never a crash):

```
option_minute/
  source=orats_1m/symbol=SPY/year=2024/month=03/SPY_2024-03-14.parquet
  _manifest/symbol=SPY.parquet     # one row per trade_date: status, n_snapshots,
                                   # n_rows, has_0dte, min/max snapshot time, sha256
```

- **Idempotent.** A day whose manifest row is `complete` with a matching sha is
  skipped, and re-running a backfill only fills gaps.
- **Postgres gets only the manifest summary** (migration `083_option_minute_manifest.sql`),
  so `get_data_readiness` can report a new dataset row:

  `option_minute_orats` — usable, `asOfUtc`, days covered, gap count.

### 3.3 Daily top-up

Add a scheduled job at 22:30 UTC on weekdays that ingests the previous trading
day. **The day being captured is still the previous one:** the "15 minutes ago"
rule makes same-day completeness impossible.

---

## 4. Study 0 — implied vs realised (run this first, before any strategy)

This is the one number the 24 Oct model backtest could not supply. If it shows
no premium, §5's short-premium grid is pointless and should not be built.

For each day with a same-day expiry, and for each entry time
`T ∈ {09:35, 10:00, 10:30, 11:00, 12:00, 13:00, 14:00}`:

1. Take the ATM strike `K` nearest the ORATS `stockPrice` at `T`.
2. **Straddle price,** with the mid and the natural fill side stored separately:
   - mid = `callMid + putMid`
   - natural = `callAsk + putAsk`, i.e. paying the ask on both legs to buy
3. **Realised move:**
   - `|S_close − K|`, with `S_close` the 16:00 SPY price from TradePro's own 1m
     bars where they exist, otherwise the ORATS 15:59 `stockPrice`, flagged
   - also `|S_close − S_T|`
4. **Report**, per entry time, per year and per VIX tercile:
   - mean and median of `straddle_mid − realised`, in $ and as % of the straddle
   - % of days where realised exceeds the straddle
   - the straddle's implied vol (ORATS `smvVol`) against realised vol from
     TradePro 1m bars over the same window

Output: `docs/research/ORATS_STUDY0_IMPLIED_VS_REALISED.md`, with the code SHA
and data manifest SHA in the header, written to `run_log` like the
wheel-backtest runs.

**Decision rule, stated now:** proceed to the short-premium half of §5 only if
the median `(straddle_mid − realised) / straddle_mid` is above **+5%** at the
09:35 entry, **and** above 0 in at least 4 of the 5 calendar years (2020H2
through 2026). Otherwise build only the long-premium half (§5.2, rows L).

---

## 5. Replay engine — `quant_engine/options/minute_replay.py`

### 5.1 Mechanics

- **One position per symbol-day.** Entry at grid time `T`, then mark-to-market at
  every stored snapshot after it.
- **Fills, as a parameter:** `fill_model ∈ {natural, mid, mid_minus_k}`.
  - **Default `natural`:** buy at the ask, sell at the bid.
  - **`mid_minus_k`:** concede `k` × half-spread per leg (default k = 0.5).
  - Every reported result shows `natural` and `mid` side by side. A strategy that
    only works at mid does not pass.
- **Costs:** commission per contract from `fees.py`. Add an IBKR US options row
  if one is missing, configurable, and check it against the owner's actual
  statement. Then exchange/regulatory fees per leg.
- **Settlement:**
  - SPY/QQQ/IWM: if held to 16:00, settle on the 16:00 underlying, with
    pin/assignment risk flagged on any short leg within $0.10 of the money.
  - SPX: cash-settled on the official close, so no assignment risk. That is the
    reason to include it.

### 5.2 Strategy grid (pre-registered; do not add rows after seeing results)

| ID | Structure | Strikes |
|---|---|---|
| S1 | Iron fly | short ATM call + put; wings ±W, W ∈ {3, 5, 8} SPY points (scale by price for QQQ/IWM; ×10 for SPX) |
| S2 | Iron condor | shorts at ±{0.5σ, 1.0σ} of the day move implied at entry; wings +{3, 5} |
| S3 | ATM call butterfly | ±{2, 3, 5} |
| S4 | Short strangle (naked) | ±1.0σ. **Reference only:** it can never pass F1 (§6) and is run so the defined-risk versions have a comparison |
| L1 | Long ATM straddle | ATM |
| L2 | Long ATM strangle | ±0.5σ |

**Exits, as a grid, applied to every row:**

- **Take-profit:** {25, 50, 75}% of max profit for credit trades, {+50, +100}%
  of debit for long ones, plus `none`.
- **Stop:** loss of {1×, 2×} credit, or −50% of debit, plus `none`.
- **Time stop:** {14:30, 15:30, 15:55 ET, hold to expiry}.

**Entry times:** {09:35, 10:00, 11:00, 13:00}.

**Optional filters, each tested as its own branch and not stacked:**

- VIX at the open ≤ 14 (matches the existing paper-strangle gate)
- the gap from the prior close is under 0.3%
- no FOMC/CPI/NFP that day (from `catalysts`)

### 5.3 Where trigger prices come from

Stops and take-profits that depend on the **underlying** (e.g. "SPY < 760") use
TradePro's own 1m bars (`bar_cache`, harvested from 8 Jul 2026, plus Yahoo 1m
backfill where it exists). Before that date, fall back to ORATS `stockPrice`,
flagged `trigger_source='orats_parity'`. Option-value exits use ORATS quotes only.

---

## 6. Gates — `ORATS_0DTE_GATES_V1.md` (commit before the first run)

Follows `GATE_TEMPLATE.md`. Every gate is evaluated on **natural fills**.

**Group A — is the edge real?**

| # | Test |
|---|---|
| V0 | ≥ 1,000 trades per (structure, exit, entry) cell. SPY 0DTE alone gives ~950–1,000 days, so pool SPY+QQQ+IWM or say it fails |
| G1 | Win rate stated per structure, before the run |
| G2 | Mean net P&L per trade > 0 after costs, at natural |
| G4 | Top 1% of trades ≤ 25% of net profit |
| G5 | Worst single day ≤ 3× mean daily credit for defined-risk rows |
| Split | Positive in **all four cells**: time split (2020-08 to 2023-06 vs 2023-07 to now) × symbol split (SPY vs QQQ/IWM) |
| Grid | **Multiple-testing guard.** The grid is about 1,500 cells. Report how many pass, compared with how many would pass by chance, from a shuffle of entry dates. A winner is only interesting if its neighbouring cells (adjacent wing, TP or entry time) also pass |

**Group B — can it be funded?**

| # | Test |
|---|---|
| F1 | Max loss per trade is defined and ≤ 1% of account NLV at the proposed size |
| F2 | Max drawdown over the full window ≤ 20× mean daily P&L |
| F3 | Longest losing streak (days) reported, along with the money lost over it |

---

## 7. Part B — fix the wheel backtest with real EOD quotes

The 11 Aug hand-check catalogued four flaws in `wheel_backtest.py`:

1. It runs on dividend-adjusted prices.
2. It has no premium floor.
3. Assignment happens only at expiry.
4. IV comes from trailing realised vol, which mispriced the META put sold the
   day after its −26% gap.

Flaws 1 and 4 come from having no historical quotes.

- **Add `hist/strikes` EOD ingest** (one call per symbol-day, back to 2007) for
  the wheel universe. It goes in the same Parquet layout under
  `source=orats_eod/`, filtered to 7–60 DTE and 0.10–0.50 |delta|.
- **Add `quote_source ∈ {model, orats_eod}` to `wheel_backtest`.** With
  `orats_eod`, strikes, premiums and IV come from the real chain in unadjusted
  price space. This closes flaws 1 and 4 without touching the gates file. The
  committed thresholds in `WHEEL_BACKTEST_GATES_V4.md` stay where they are.
- **Re-run the V4 suite on both sources** and commit the comparison. This is
  also the external cross-check of TradePro's numbers the owner asked for on
  22 Aug.

Budget: ~250 symbols × ~4,900 days ≈ 1.2M calls. That's more than a month of
quota, so spread it over two months, or start with the 82-name wheel universe
(~400k calls).

---

## 8. Read-only surface

- **MCP tool `get_orats_coverage(symbol)`:** manifest summary (first/last day,
  gaps, `has_0dte` days, rows).
- **MCP tool `get_study0(symbol, entry_time)`:** the §4 table.
- **`get_data_readiness`:** two new rows, `option_minute_orats` and
  `option_eod_orats`.

No new UI until a §6 cell passes. A UI over unvalidated numbers is just a faster
route to wrong answers, the same rule that applied to the wheel-backtest panel
on 11 Aug.

---

## 9. Build order and acceptance

1. **Provider plus secret, then one live call per endpoint.** Save the raw CSV
   headers under `strategies/tests/fixtures/orats/`.
   *Accept:* the parser test passes on the fixtures, the token is absent from all
   logs, and a 429 triggers backoff (unit test with a fake server).
2. **SPY backfill, last 60 trading days only.**
   *Accept:* manifest complete, `has_0dte` true every weekday, and spot-check 3
   snapshots against the owner's IBKR fills or TWS history for 23 Oct 2026.
3. **Study 0 on that 60-day slice.** This is a sanity read, not a decision.
4. **Full SPY backfill, then Study 0 on the full history.** *Decision point (§4 rule).*
5. **Replay engine plus §6 gates file committed,** then the grid on SPY+QQQ+IWM.
6. **Part B** (EOD ingest plus wheel re-run). This can run in parallel with steps
   4–5, since it uses a separate quota slice.

**Tests (python suite):**

- CSV parse
- strike filter (±4%, dte ≤ 7)
- manifest idempotency (re-run makes zero calls)
- fill-model arithmetic (natural is always ≤ mid for a credit trade)
- settlement at 16:00
- SPX cash-settle path
- gates file hash recorded in `run_log`

---

## 10. Open questions for the owner

1. Which ORATS plan exactly? The answer decides whether Part A exists (see the
   header).
2. Include SPX? It's cash-settled with no assignment, and it suits a company
   account. Check whether IBKR permissions on the CCIT account already cover
   index options.
3. Should the IBKR commission in `fees.py` come from a recent statement, rather
   than the published rate card?
