# TradePro review — options and stock signals (weekend 28–29 Nov 2026)

**Purpose.** A build list for the next two days, written from a week of
managing the live book by hand (23–27 Nov). Every item below is something an
agent had to work out manually in conversation that TradePro should have put
on a screen. Read with `docs/LIVE_BOOK_LESSONS_2026_11.md` (the lessons) and
`strategies/INCOME_PUT_SPREADS_V1.md` (the income engine already specced).

**Owner basics still apply** (SHARED_CONTEXT.md): read-only on the live
account, no order tickets for it, event blackout, 40–45% sector cap, no naked
puts, sector and index context on every view.

**What already exists and should be reused, not re-written:**
`cli/live_risk.py` (Flex read of the live book, close verdicts,
concentration, assignment), `quant_engine/options/manage.py` (ManageRules),
`quant_engine/options/iv_rank.py`, `black_scholes.py`, `chains_ibkr.py`,
`quote_sanity.py`, `income_spreads.py` (income scan, monitor, ledger),
`cli/put_check.py` (historical put outcomes), `sector_stress.py`,
`macro_regime.py`, `catalysts*.py`, `intraday_setups.py`.

---

## Operating model — owner-agreed summary (28 Nov 2026)

This is the frame every item below serves. Agreed with the owner on
27–28 Nov after reviewing six months of live trading: the method (income
from options around a share book) worked in calm months; **concentration,
naked puts on margin, leverage and shares held with no stop** turned a sector
correction into a deep drawdown. TradePro's first job is to make those four
impossible to miss.

### A. Hard risk limits — no unlimited risk, ever
| Limit | Value |
|---|---|
| New short options | Spread or covered by shares. Never naked on margin. |
| Cash-secured puts | Allowed, max **2 open**, only on want-to-own names, cash covers assignment after the reserve, and only when the market is **not in a downtrend** (SPY and QQQ above the 50-DMA, sector gate passes). |
| Max loss per income trade | $3k (about 1–1.5% of net liquidation) |
| Income book total max loss | **£12k now; £20k once naked puts are below five** (replaces the £6k phase-1 cap) |
| Minimum credit / width | **20–25% for index and low-volatility names; ⅓ for volatile names** (30-day HV above ~40%) |
| Sector cap | 40–45% of exposure, option-adjusted (item 1) |
| Leverage | ≤ 1.6× |
| Margin cushion | Excess liquidity ≥ 30% of net liquidation; alert at 35%; below 30% cut risk before anything else |
| Cash reserve | $50k untouched; assignment cash checked month by month (item 17) |
| Account floor | Net liquidation floor set by the owner (settings-kv), measured on the RTH close |
| Macro events | Stress test before every FOMC/CPI (item 14) |

### B. Stock positions need stops (the biggest loss came from here)
Owner, 28 Nov: "our main drawdown was on stocks due to no stop loss."
- **Every new stock position carries a stop written at entry**: a closing
  level (below the 50-DMA or the entry support, or 2× ATR), sized so the
  loss at the stop is ≤ 2% of net liquidation.
- **A swing never becomes a hold by default.** If the stop is hit, the
  position is flagged EXIT; converting it to a long-term hold requires an
  explicit owner decision recorded in the journal (item 16).
- **No averaging down** below the stop, and no new money into a name whose
  sector gate is failing.
- **Existing underwater shares** (bought before these rules): TradePro shows
  each one's distance to its last capitulation low and flags a weekly close
  below it as "thesis broken — review". The action stays the owner's.
  *(Open decision: whether a break means cut half, cut all, or collar.)*

### C. The three-check gate — in front of every proposal
1. **Market and sector trend** (item 13): SPY/QQQ and the sector ETF vs the
   50-DMA, lower-lows flag, rate-odds trend (item 14).
2. **Events before expiry** (item 3): earnings, FOMC, CPI.
3. **Size and structure**: section A's limits.
Each check shows pass/fail **with its number**. A failed check blocks the
proposal; it is never hidden.

### D. Two engines
- **Recovery** — the shares already held, covered calls at 0.20–0.30 delta
  on green days (item 4), and the put-book manager (item 17): hold / accept
  assignment / repair into bounces. No automatic rolls: close, then any new
  position is a fresh trade through the gate.
- **Income (continues, diversified)** — ~40% index spreads (IWM, XSP, QQQ
  call spreads, iron condors when calm), ~40% non-tech single names, ~20%
  covered calls; 1–2 new trades a week, expiries staggered 4–8 weeks; sell
  after events; take profit at 50%, close at 21 DTE or on a close through
  the entry level (items 7 and 12).

### E. Want-to-own flags (drive the put-book manager and CSPs)
Owner-set per symbol in settings-kv. Starting values: **want** — NVDA, AMD,
STX, WDC, SNOW, HOOD; **no more of** — GOOGL (already 200 shares), MRVL,
SKHY, DELL (beyond the Mar 380), CRDO.

### F. Symbol view and charts (item 15)
Default **daily** for decisions, **5-minute intraday with VWAP** for
timing. Price panel: candles, 20/50/200-DMA, Bollinger bands,
support/resistance, volume. Intraday: VWAP, day high/low, gap, volume pace.
Indicators: RSI, ATR, realised vs implied volatility, IV rank, expected move.
Context: stock vs sector ETF vs SPY vs QQQ rebased to 100, beta, correlation,
relative strength. Header line: futures, sector regime, next event. Event
markers on the axis. Clear on a phone.

### G. How it is built
Deterministic code for every number and gate (no LLM in the calculation
path); the agent reads TradePro's outputs through the tools of item 16 and
cites them. Failed strategies are parked (see "Cut or park"). TradePro
never places or prepares live orders.

---

## Priority 1 — build this weekend

### 1. Portfolio exposure in dollar-delta, by segment
**Seen this week.** Exposure was worked out by hand with a script: about
+$237k net delta (≈90% of net liquidation), semis/memory +$215k, software
+$107k, DELL +$48k, hedges −$137k. `live_risk.py` reports capital by sector
but not option-adjusted exposure, so short puts and hedges are invisible in
the concentration number.
**Build.** In `live_risk.py`: for every position, delta-dollars =
quantity × delta × multiplier × underlying price (shares: delta 1). Group by
the segment map (semis/memory, software/internet, hardware/AI infra, index
hedges). Report gross long, hedges, net, net as % of net liquidation, and a
"1% move = £X" line. Flag any segment over the 40–45% cap (basic 5).
**Accept when.** On the 27 Nov book it reproduces the numbers above within 5%,
and greeks missing from the feed are shown as a named gap, not as zero.

### 2. Naked-put repair board
**Seen this week.** 14 naked puts; each repair (floor, roll down-and-out,
close) was priced by hand. MDB: closing (−$2.7k) vs a 300 floor (coin flip
±$2k). PLTR: a 19% bounce made closing cost only −$316. ManageRules says
CLOSE/WATCH/HOLD but does not price the alternatives.
**Build.** For every naked short put, one row with four priced options from
live chains: (a) close now, (b) buy a floor ~10% below the short strike,
(c) roll down-and-out to the next monthly for ≥ 0 net credit, (d) hold. For
each: cash now, max loss, P&L at expiry in four scenarios (+15%, flat, −10%,
−20%), and the event check (earnings or FOMC before expiry). Highlight
"cheap exit" when the close cost is within 10% of the premium collected.
Sort by urgency: expiry ≤ 21 days and deep ITM first (NVDA 205, CRDO 150 on
the current book).
**Accept when.** It reproduces the MDB and PLTR numbers above from 27 Nov
quotes, and never presents a choice without its worst case.

### 3. Event calendar inside every option's life
**Seen this week.** MDB earnings (1 Dec, after close) was found by web
search; ORCL (~10 Dec, unconfirmed) likewise; the mid-December Fed meeting
was remembered, not checked. MDB's September −22% move was a CEO
resignation, not earnings — a naive history mixes them.
**Build.** One function `events_between(symbol, today, expiry)` returning
earnings (with confirmed/estimated flag), FOMC, CPI and other dated macro
events. Every option row (positions, repair board, income scan, covered
calls) shows the events inside its life. For earnings history, store
earnings-day moves separately from other large moves, so the "typical
earnings move" is not polluted by non-earnings shocks.
**Accept when.** On 27 Nov it lists MDB 1 Dec (confirmed) inside the Dec 18
expiry, ORCL ~10 Dec (estimated) inside Jan 15, and FOMC inside both, and
MDB's earnings-move history excludes the 29 Sep CEO-resignation day.

### 4. Covered-call manager
**Seen this week.** Calls sold by hand: SNDK 105C at 0.30 delta into
strength, ORCL skipped while fading, GOOGL waiting for a 282 trigger, MU and
SNDK checked for the 0.5-delta roll-up rule.
**Build.** For every 100-share lot: covered or not, and if covered the
call's delta, days to expiry and roll flag (delta > 0.5 → roll up/out;
premium < 20% left → roll down). For uncovered lots, propose the ~0.30-delta
call for the next monthly that clears events, only when the stock is up on
the day and above VWAP ("sell into strength"), and show the strike against
the owner's cost basis so a strike far below basis is visible.
**Accept when.** On 27 Nov it shows SNDK fully covered (5× 95C, 3× 105C),
MU covered (Feb 760C, 0.38 delta, no roll), GOOGL 1 of 2 lots uncovered, ORCL
uncovered with "stock fading — wait".

### 5. Hedge module sized to remaining exposure
**Seen this week.** The SMH hedge was bought in a panic after a −7% day and
financed with a short call spread, which then doubled its loss in the rally.
A second (January) SMH put spread was correctly NOT bought because the
December package still covered the risk.
**Build.** From item 1's segment exposure, propose a plain put spread (no
short call leg) on the matching index (SMH semis, IGV software, QQQ
mega-cap) sized to cover a stated share of a −10% segment move, net of hedges
already held. Suppress new hedge proposals on a day the segment fell ≥ 5%
(reprice next calm session). Track each ETF option's must-close date (UK
retail cannot hold the ETF shares, so an ETF option must be closed before
expiry rather than exercised or assigned).
**Accept when.** On 27 Nov it proposes no new SMH hedge (existing cover
≈ 40% of semis exposure), proposes an IGV hedge for the unhedged software
segment, and lists the SMH package must-close date (~14 Dec) and the QQQ
Dec11 call spread (~10 Dec).

---

## Priority 2 — if time allows

### 6. Volatility context on every option view
IV vs 20/30-day realised volatility, IV rank (`iv_rank.py` exists), and the
expected move to expiry from the at-the-money straddle. This week the key
facts were all volatility: MDB implied ±15% vs a ±15–20% earnings history;
DELL realised 131%; MDB put time value ~$25 of a $48 price.

### 7. Call credit spreads at resistance (the bearish income generator)
The income engine only sells put spreads (bullish). With the book ~$237k
net long, the useful income this week was a QQQ 660/670 call spread sold at
a rejection of 647. Add a call-spread scan: index ETFs and names NOT held
long, short strike above a measured resistance (prior high / 50-day), credit
≥ ⅓ of width, max loss ≤ $3k, event check, written stop (close above the
resistance level).

### 8. Intraday read: gap, fade and volume pace
Gap from prior close, distance to VWAP and the day's high/low, and volume so
far vs the same time on an average day (not vs a full day — the first hour is
always heaviest, and half days need their own curve). Flag "first 15
minutes" so nothing is proposed before open + 15 minutes. Verify the IBKR
volume field's units per instrument before using it (it was ambiguous this
week).

### 9. Progress tracker on net liquidation, not unrealised P&L
Net liquidation vs the £180k floor and the £200k target milestones (£186k
Dec 31, £191k Jan 31, £196k Feb 28, £200k Mar 31). Show deposits from the
Flex cash report, not reconstructed from TWR (the 5 Nov TWR was distorted by a
deposit). Unrealised P&L (£137k on 27 Nov) is against entry prices and was
mistaken for money lost from deposits.

### 10. Data honesty for options
Label option marks as stale before 14:30 London and through US holidays
(Thanksgiving marks were frozen), and mark a quote unusable when the
bid/ask spread is wider than 15% of mid (`quote_sanity.py`). A combo labelled
"Bull Call" with side SELL is a bear call spread — show the economic
direction, not the broker's label.

---

### 11. US stocks 23x5 from 6 Dec 2026 (IBKR notice, 27 Nov)
IBKR extends US stock trading to 23 hours a day, 5 days a week from Sunday
6 Dec 2026 (subject to final regulatory readiness). Stocks trade 21:00 ET to
20:00 ET the next day (London 02:00 to 01:00), with a 20:00-21:00 ET pause
where only IBEOS / Blue Ocean trade; the dedicated OVERNIGHT venue is
retired (orders sent to it are rejected; SMART routes overnight); trades
after 20:00 ET count toward the next trade date. **Options are unchanged:
regular hours only.**

Checked in the repo on 27 Nov: nothing routes to OVERNIGHT, and IBKR history
requests use `outsideRth=false`, so daily bars stay RTH. Four things need
work:

- **Option-chain capture window.** `cli/option_chain_capture.py` captures
  between 16:30 and 04:00 ET on the assumption that pre-market repopulates
  the chain at 04:00 ET. If the source now refreshes at the 21:00 ET
  pre-market start, captures after that come back with OI/bid/ask dark.
  After 6 Dec, verify when the chain repopulates and move
  `CAPTURE_SHUT_ET` (probably to 21.0) if needed; keep the
  `MIN_OI_COVERAGE` guard as the detector.
- **Overnight move labelled with its volume.** "Pre-market price" now exists
  from 02:00 London. Every brief/alert that shows an overnight or pre-market
  move shows the volume behind it and labels thin moves, so a 03:00 print on
  low volume is not read as a signal for the 14:30 open.
- **Stock marks live, option marks frozen.** Overnight, net liquidation and
  position P&L move on stock prices while option marks stay at the last RTH
  close. Label option marks stale outside 14:30-21:00 London (extends
  item 10), and measure the floor, milestones and daily P&L on the RTH close.
- **Trade date.** Ledger and P&L date any fill after 20:00 ET to the next
  trade date.

### 12. Income engine: why the 27 Nov scan found 0 of 14
The latest `income_scan` (published 27 Nov, bars to 25 Nov) returned no
candidates. Half the rejections are correct (JPM, GOOGL, UNH, CAT, PEP, XOM,
MRK below the 200-DMA or the cloud; UNH and CAT flagged falling knives). The
rest are design gaps:

- **Low-volatility names can never pass.** JNJ, PG and HD are in clean
  uptrends, but "short strike below the 30-day low" plus "short delta
  0.15–0.30" is unsatisfiable for quiet stocks (strikes below the 30-day low
  sit at Δ 0.05–0.09). Fix: for names with 30-day HV below ~30%, use the
  20-day low, or let the delta floor drop to 0.10 when the strike is below
  the swing low. Pre-register the change (house rule) before it gates.
- **The index half of the engine is not scanned.** The spec says 50% index
  options (QQQ, XSP, IWM); none appear in the scan. IWM and XSP are the
  diversified, non-AI legs this book needs most. Add them (ETF options must
  be closed before expiry for this account — see item 5).
- **One expiry only.** Every row tried 15 Jan. Try the next two monthlies
  (e.g. Jan and Feb) and keep the best that clears events; V failed on thin
  credit (11.6% < 15%) and would likely pass at a later expiry.
- **Stale held list.** `held_live` still listed MDB and PLTR on 28 Nov,
  after both were closed on 27 Nov. Note the Flex snapshot time on the scan.
- **Owner's credit rule.** The live rule since 25 Nov is credit ≥ ⅓ of
  width for put spreads; the engine's `min_credit_to_width` is 0.15. Show
  both on the row and let the owner set the value in settings-kv.

Accept when: a rescan of the same names on Monday's quotes tries two
expiries and the index ETFs, and every rejection still names its number.

### 13. Stock vs sector vs market — correlated stats on every decision
Owner, 28 Nov: decisions must check the stock against its sector index and
SPY, with correlation statistics, not the stock's own price alone (owner
basics 7). `relative_context.py` and `sector_rs.py` already compute
21/63-session relative strength vs the sector ETF and SPY, display-only.
Extend that to:

- **Beta and correlation** to the sector ETF and to SPY (60 and 120
  sessions), so a stock's move is split into "market", "sector" and "own".
- **Sector regime line** for the sector ETF itself: above/below 50-DMA and
  200-DMA, lower-lows flag, 20-day return — the check that would have caught
  the October semis breakdown (SMH) before more puts were sold.
- **Market regime line** for SPY and QQQ: the same fields.
- **Relative strength rank** within the sector (stock's 63-day return minus
  the sector ETF's), so "MU lagging its bucket while STX ripped" is a number.
- **Shown on every surface that proposes or manages a trade:** income scan
  rows, the repair board (item 2), covered calls (item 4), swing candidates.

Gating: the sector regime check becomes a GATE for new short puts and put
spreads ("no new short puts while the sector ETF is below its 50-DMA and
making lower lows"), but only after a pre-registered gates doc and a replay
on the Sep–Nov 2026 semis period shows it would have blocked the October
entries without blocking the summer ones.

Accept when: on the 27 Nov book, MU, SNDK, STX and WDC rows each show beta,
correlation and RS vs SMH and SPY, and SMH's regime line reads correctly
for October (below 50-DMA, lower lows) and late November.

### 14. Macro event stress test, rate-odds regime and implied moves
Seen this week: the 9 Dec FOMC (decision 14:00 ET / 19:00 London, with new
projections and the chair's press conference; November CPI the next day) was
not on any screen. Market-implied odds of a December hike rose from ~22% in
late August to ~90% by late November — the same weeks tech fell hardest —
and nothing read that trend. A rough stress of the 27 Nov book put a hawkish
surprise (QQQ −4%, semis −7%) at about −$15–18k, i.e. net liquidation at the
£180k floor.

- **Event list.** FOMC, CPI, payrolls (and other scheduled macro) in the
  event calendar of item 3, so every option row names them.
- **Rate-odds regime input.** Store the market-implied probability of the next
  FOMC outcome daily (CME FedWatch or a prediction-market feed) and its
  20-day change. Rising hike odds together with QQQ or the sector ETF below the
  50-DMA = "reduce exposure, shrink new put selling" flag.
- **Implied move, not a guess.** Before each event, read the expected move
  from the at-the-money straddle on SPY/QQQ expiring just after it, and the
  sector ETFs' equivalents.
- **Stress test before every FOMC/CPI.** Run the book (item 1's dollar-delta
  by segment, with a gamma adjustment for short puts) through three scenarios:
  base (priced outcome), hawkish shock (implied move down, sector beta
  applied) and dovish relief. Show net liquidation for each against the
  £180k floor; flag when the hawkish case breaches it or leaves less than
  £15k of cushion.
- **After the event.** Show the futures reaction and the fall in implied
  volatility, so premium selling restarts after the event, not before.

Accept when: for 9 Dec 2026 it lists the meeting on every option row whose
expiry is later, shows the priced odds and their trend since August, and
reproduces the −$15–18k hawkish-shock estimate on the 27 Nov book.

### 15. Charts and symbol views with sector, index and futures context
Owner, 28 Nov: "when we see data chart of any symbol it should also show its
sector and futures and some index." Every symbol chart and symbol view
(symbol workup, market state, swing and income rows) shows, on the same time
axis:

- the symbol, its sector ETF (from the `symbol_sector_etf` map), SPY and
  QQQ, **normalised to 100 at the window start** so relative performance is
  visible at a glance;
- the index futures (ES, NQ) for the current session, including the
  overnight move — with the 23x5 caveat of item 11 (thin volume overnight);
- the sector ETF's and SPY's 50/200-DMA state as a one-line regime badge,
  plus item 13's beta, correlation and relative-strength numbers;
- markers for events in the window (earnings, FOMC, CPI) from item 3.

**Clarity rules (owner: "the chart should be clear looking at any stock").**
More context must not mean more clutter:

- **One answer at a glance.** A one-line header above the chart reads, e.g.,
  "MU −4.1% today · SMH −3.2% · QQQ −1.1% · NQ −0.9% · sector below
  50-DMA · earnings in 18 days". If the reader stops there, they have the
  picture.
- **Two panels, not one crowded one.** Top: the stock's own candles with its
  50/200-DMA and support/resistance levels. Bottom: the normalised
  comparison lines (stock, sector ETF, SPY, QQQ). Volume as a thin strip.
- **Four lines at most** in the comparison panel; the stock line is the
  boldest, the others thin and muted. Futures appear as a figure in the
  header, not as another line.
- **Colour means one thing.** Green/red only for up/down; one fixed colour per
  benchmark across every chart (SPY always the same grey, QQQ the same blue).
- **Direct labels** at the right edge of each line ("MU −38%", "SMH −21%")
  instead of a legend to decode.
- **Events as small markers** on the time axis (E = earnings, F = FOMC,
  C = CPI), with a tooltip, never as text over the price.
- **Sensible default window** (6 months daily), with 1M / 3M / 1Y switches;
  the comparison panel rebases to 100 at the start of whatever window is
  shown.
- **Works on a phone.** The header and the top panel must read clearly at
  phone width; the comparison panel can sit below.

Accept when: the MU chart for Sep–Nov 2026 shows MU vs SMH vs SPY vs QQQ
normalised, the October SMH breakdown is visible against MU, and the FOMC
and MU earnings dates are marked.

### 16. Agent support API — what the trading agent needs from TradePro
Owner, 28 Nov: "what would help next time that TradePro can provide which
you can't do from IBKR alone?" This week most agent time went into
rebuilding these from raw IBKR calls, one at a time. Expose each as a
read-only MCP tool (deterministic numbers only; the agent cites them and
never recomputes them):

1. **Trade journal + effective basis** — per position: open date, entry
   reason, premium received, rolls, realised P&L, effective basis
   (strike − net premium), and the stop/target written at entry. IBKR only
   shows the current position.
2. **Event calendar** — item 3 (earnings confirmed/estimated, FOMC, CPI)
   for any symbol and date range.
3. **Whole-book exposure in one call** — item 1 (dollar-delta by segment,
   greeks filled where the broker feed is blank, gamma-adjusted stress).
   IBKR caps snapshots at 100 data points and left DELL/WDC/SNOW greeks
   empty on 27 Nov.
4. **Option history** — harvested IV, IV rank/percentile and past chains
   (OPRA gives no history).
5. **Pre-computed technicals** for the universe and sector ETFs: 20/50/200
   DMA, RSI, 20/30-day lows, support/resistance, plus item 13's beta,
   correlation and relative strength.
6. **Clean earnings-move history** — item 3 (earnings days separated from
   other shocks).
7. **Close-based conditional triggers** — rules like "DELL daily close
   < 265" or "QQQ close > 650", evaluated at the RTH close with a push
   notification when one fires, and a list of active triggers. IBKR price
   alerts cannot express closing conditions.
8. **Cash flows and true performance** — deposits/withdrawals from the Flex
   cash report and net-liquidation history, so performance is not
   reconstructed from TWR (distorted on 5 Nov).
9. **Freshness labels** — item 10/11: live / stale (pre-open, holiday,
   overnight) / unusable (spread > 15% of mid) on every option mark.
10. **Pre-screened candidates with live pricing** — item 12's income scan,
    item 7's call spreads at resistance and item 4's covered calls, each
    with the three-check gate result (market/sector trend, events,
    size/structure).

Accept when: the agent can answer "what is our exposure, what is expiring
or reporting in the next 21 days, and which triggers fired today" with three
tool calls and no hand-built scripts.

### 17. Put-book manager — the owner's assignment-or-repair policy as code
Owner, 28 Nov: "all these steps should be baked into TradePro." Every short
put is classified daily by fixed rules; the only human inputs are a
per-symbol **conviction flag** (want to own / neutral / don't want) and the
cash reserve. Builds on items 1, 2, 3 and 16.

**Inputs per put:** strike, premium received (from the journal, item 16),
expiry, underlying price, delta, events before expiry (item 3), sector,
the conviction flag.

**Effective cost** = strike − net premium received. **Gap** = (price −
effective cost) / effective cost.

**Classification (first match wins):**
1. **Event first:** earnings inside expiry and expiry ≤ 30 days → CLOSE
   before the event (CRDO 150, 27 Nov).
2. **Don't-want flag** → REPAIR.
3. **Gap ≥ 0** → HOLD (time decay is on our side; may expire worthless).
4. **Gap 0 to −10%, want-to-own, cash test passes, sector cap holds after
   assignment** → HOLD / ACCEPT ASSIGNMENT.
5. **Gap below −10%**, or a single name that would exceed its cap after
   assignment (e.g. a third GOOGL lot) → REPAIR.

**Repair = close into a bounce, not on a down day.** Raise "cheap exit
now" when any holds: the put's buy-back cost is within 25% of the premium
received; the underlying has risen ≥ 8% from its 10-day low; or it is back at
its 20-DMA. Never propose a repair close on a day the sector ETF is down
≥ 3%.

**Cash calendar.** Group puts by expiry and show cumulative assignment cash
(strike × 100) against cash minus the $50k reserve, month by month. When a
month would exceed it, rank that month's puts by gap and propose closing
the lowest-ranked ones ahead of expiry (27 Nov: January = AMD $45k + SNOW
280 $28k against ~$40k left after NVDA).

**Floors.** For HOLD/ACCEPT names, propose a floor below the measured
support (item 5's plain long put) on a green day, and immediately if the
underlying closes below that support (STX ~450, WDC ~260, DELL 265).

**Hedge coverage gap.** Compare the latest expiry of the short puts with
the latest expiry of the hedges; flag the months where puts are open but no
hedge covers them (27 Nov: hedges end ~14 Dec, puts run to March).

**After assignment.** Hand the new lot to the covered-call manager
(item 4): 0.25–0.30 delta, green days, strike above the effective cost
where possible.

Accept when, on 27–28 Nov prices, it reproduces: HOLD — SNOW 270/280, STX,
WDC (gap ≥ 0); HOLD/ACCEPT — AMD (−3%), HOOD (−8%), NVDA (owner decision);
REPAIR — GOOGL 315 (single-name cap), SKHY (−11%), MRVL (−20%), DELL 420
(−19%); CLOSE — CRDO (earnings 1 Dec); and the January cash shortfall.

---

## Stock signals — what to change

- **Index and sector context on every signal (basic 7).** Each stock view
  shows the stock's day and 20-day move next to its sector ETF and QQQ/SPY,
  plus beta. A stock down 5% on a −5% sector day is a sector move, not a
  stock signal.
- **Catalyst first.** TRUST_STATUS names "zero catalyst awareness" as the
  biggest gap. Use item 3's calendar plus IBKR news on every BUY/WAIT/AVOID
  row; a signal across an event says so.
- **Technical exits written at entry.** Every signal carries its stop level
  (a closing level, e.g. "close below 160") and target, like the PLTR and
  QQQ trades this week.
- **Recovery-exit flag for short puts.** When a held short put's underlying
  rallies back to breakeven, raise "cheap exit available" (PLTR, 27 Nov).

---

## Cut or park — make room for the build

Owner, 28 Nov: "we can also cut stuff from TradePro that is not worth
pursuing." Criteria: a strategy whose pre-registered study failed, or a
surface that produces trade ideas nobody acts on, is **parked**: its result
doc stays (it is the learning record), but its candidate feed comes off the
screens, the emails and the scheduled jobs, so it stops costing compute,
data and attention.

Candidates to park (verdicts as recorded in the repo):

- **Equity trend** — `EQUITY_TREND_GATES_V1_RESULT.md`: FAILED.
- **News shock** — `NEWS_SHOCK_GATES_V1.md`: FAILS.
- **Post-earnings puts / wheel funding** — `POST_EARNINGS_PUT_GATES_V1.md`:
  does not license funding; wheel v3 verdict DO NOT FUND stands.
- **Earnings v2** — `EARNINGS_GATES_V2_RESULT.md`: failed.
- **Early entry** — `EARLY_ENTRY_GATES_V1.md`: REJECTED.
- **Buy-the-sell-off playbook** — O3 NOT SUPPORTED (SELLOFF_PLAYBOOK_GATES_V1),
  apart from the high-beta tercile.
- **Congress trades (Quiver)** — `QUIVER_CONGRESS_GATES_V1.md`: fails.
- **Small intraday scalps/shorts** with £100–200 payoffs — the owner does not
  want them (23 Nov); keep only setups whose realistic target is ≥ £1k.
- **Short-side strategy** — already parked (`SHORT_STRATEGY_PARKED.md`).

Keep and focus on: the defined-risk income engine, momentum rotation (in
forward test), the live-risk lane, and this review's items 1–5 and 12–16.
**Pause new strategy studies for 2–3 months** while the forward tests run.

Before removing anything, list each parked strategy's live touch-points
(scheduled jobs, email digest sections, MCP tools, UI panels) and switch them
off by config where possible, so a revival is a settings change, not a
rewrite.

## Out of scope this weekend

New strategies, new data vendors, and anything that places or prepares live
orders.
