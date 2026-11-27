# Live-book lessons — 25 and 27 Nov 2026

Source: live trading on the IBKR company account during the 25 Nov semis
sell-off and the 27 Nov half-day rebound. The full working spec (guardrails,
repair menu, hedge module, covered-call manager) lives in the owner's
"TradePro spec" doc; this file records the lessons and the rule each one
should become in TradePro, so agents in every lane can read them from the repo.

These are rules for what TradePro **flags and proposes**. Owner basics 1
still applies: TradePro never places or prepares live orders.

## Standing rule added 27 Nov 2026 (owner)

**Every new short option must have defined risk** — a spread with a long
protective leg, or a call covered by shares held. TradePro never proposes a
naked short put or naked short call. Existing naked puts are repaired one at
a time (floor, roll down-and-out, or close), on calm or green days.

## Lessons from 25 Nov (semis −7%, then reversal)

Net liquidation went £178k → £163k (low) → £179k as semis fell 7% and then
reversed.

| What happened | Lesson | Rule for TradePro |
|---|---|---|
| Naked puts on DELL, AMD, STX ran deep ITM with no floor | Repair is cheap early, expensive late | Repair check flags a naked put at 5% from strike |
| SMH hedge bought after a −7% drop, then the market reversed (−$3.1k) | Insurance bought in a panic is expensive | Standing hedge, topped up when calm |
| QQQ put spread, bought earlier and calmer, gained ~$1.3k | Early hedges pay | Standing hedge map |
| SMH would have paid ~2× QQQ for this book | Hedge with the index that matches the book | Segment-to-index map (SMH semis / IGV software / QQQ mega-cap) |
| Software (MDB earnings ~1 Dec) had no hedge | Gaps hide in segments | IGV hedge; earnings-in-expiry check |
| Calls sold at the low would have paid least | Sell calls into strength | Bounce triggers + ~0.30 delta |
| Winning calls bought back cheap on the down day, new ones sold on the bounce | Roll calls with the move | Roll-down / roll-up rules (roll up when delta > 0.5) |
| NVDA intraday short stopped out on pre-holiday short-covering | Shorts after a big down day into a holiday get squeezed | Intraday blackout: no shorts after a −5% day into a holiday or the week's last session |
| 4-leg combo rejected; order instructions can't be edited | Tool limits cost time | Split 4-leg packages into two 2-leg combos |
| SMH/QQQ shares blocked (MiFID retail / PRIIPs) | ETF options are allowed, shares are not | Close ETF options before expiry (no exercise or assignment) |

## Lessons from 27 Nov (half day)

Net liquidation went £185.6k (Thu) → £192.2k (pre-market) → £187.4k (open
fade) → £193.0k (close). Trades: MDB Dec18 340P closed (−$2,656), PLTR Jan15
165P closed (−$316), SNDK 3× Jan15 105C sold (+$1,425), QQQ 4× Dec11 660/670
call spread sold (+$1,460 credit, max loss $2,540).

| What happened | Lesson | Rule for TradePro |
|---|---|---|
| Wednesday's SMH hedge was bought in a sell-off; Friday's decisions, made on a green day, got better prices | Decide on calm or green days, not in a panic | No new hedges after a −5% day; reprice the next calm session |
| A strong gap-up open faded within minutes on the half day | The first 15 minutes are the noisiest | No proposals before open + 15 min; limits near mid |
| PLTR bounced 19% in a week, so the 165P closed for only −$316 | Recoveries are the cheap exit for naked puts | Repair check: when a naked put is back near breakeven, propose closing |
| MDB 340P before earnings: closing (−$2.7k) vs a floor (coin-flip ±$2k) | Before earnings, take the known loss unless there is a real bullish view | Earnings-in-expiry: default close; floor only with a stated view (owner basics 3) |
| SNDK calls sold into strength with a 31% cushion; ORCL skipped while fading | Sell covered calls into strength and leave recovery room | Covered calls only on up days, ~0.30 delta, strike well above the recovery level |
| Jan SMH put spread not bought because the Dec package still protects | Don't stack hedges on the same risk | Size hedges to remaining exposure; shrink them as naked puts are cleared |
| Selling a call spread to fund the SMH puts doubled the loss in the rally | A short call in a hedge is a second bet, not insurance | Hedges are plain put spreads, with no short call leg |
| QQQ broke 647 after the spread was sold; the 650 exit was already set | Set the exit before entering | Every proposal carries a written stop and target |
| Unrealised P&L showed a £137k loss while net liquidation was £192k | Unrealised P&L is against entry prices, not deposits | Progress, floor (£180k) and targets are measured on net liquidation only |
| Most of the damage came from naked short puts | Naked short options carry unlimited downside | Every new short option is a spread or a covered call — never naked |

## Account guardrails in force (for reference)

- Net liquidation floor: £180k. Target £200k by 31 Mar 2027 (milestones
  £186k Dec 31, £191k Jan 31, £196k Feb 28).
- Max loss per income trade: $3k. Put-spread credit ≥ ⅓ of width.
- No new bullish bets while the book is heavily net long one sector
  (owner basics 5: 40–45% sector cap).
- No £100–200 payoff trades.
