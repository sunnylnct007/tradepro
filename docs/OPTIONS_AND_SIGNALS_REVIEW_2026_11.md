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

Accept when: the MU chart for Sep–Nov 2026 shows MU vs SMH vs SPY vs QQQ
normalised, the October SMH breakdown is visible against MU, and the FOMC
and MU earnings dates are marked.

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

## Out of scope this weekend

New strategies, new data vendors, and anything that places or prepares live
orders.
