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
