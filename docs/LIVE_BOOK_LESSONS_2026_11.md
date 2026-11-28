# Live-book lessons — 25 and 27 Nov 2026

Source: live trading on the owner's IBKR account during the 25 Nov semis
sell-off and the 27 Nov half-day rebound. Account figures, positions and
individual trades are NOT in this file (the repo is public); the original with
them is in settings-kv `docs_live_book_lessons_2026_11`. The full working spec (guardrails,
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

Net liquidation fell about 8% intraday as semis fell 7%, then recovered most
of it by the close.

| What happened | Lesson | Rule for TradePro |
|---|---|---|
| Naked puts on several names ran deep ITM with no floor | Repair is cheap early, expensive late | Repair check flags a naked put at 5% from strike |
| SMH hedge bought after a −7% drop, then the market reversed (a loss) | Insurance bought in a panic is expensive | Standing hedge, topped up when calm |
| QQQ put spread, bought earlier and calmer, gained | Early hedges pay | Standing hedge map |
| SMH would have paid ~2× QQQ for this book | Hedge with the index that matches the book | Segment-to-index map (SMH semis / IGV software / QQQ mega-cap) |
| Software (a held name reporting within days) had no hedge | Gaps hide in segments | IGV hedge; earnings-in-expiry check |
| Calls sold at the low would have paid least | Sell calls into strength | Bounce triggers + ~0.30 delta |
| Winning calls bought back cheap on the down day, new ones sold on the bounce | Roll calls with the move | Roll-down / roll-up rules (roll up when delta > 0.5) |
| An intraday short stopped out on pre-holiday short-covering | Shorts after a big down day into a holiday get squeezed | Intraday blackout: no shorts after a −5% day into a holiday or the week's last session |
| 4-leg combo rejected; order instructions can't be edited | Tool limits cost time | Split 4-leg packages into two 2-leg combos |
| SMH/QQQ shares blocked (MiFID retail / PRIIPs) | ETF options are allowed, shares are not | Close ETF options before expiry (no exercise or assignment) |

## Lessons from 27 Nov (half day)

Net liquidation gapped up pre-market, faded at the open and closed near the
high. Trades that day: two naked puts closed (one before earnings, one into a
bounce), covered calls sold into strength, and an index call spread sold at a
rejection level. Figures and legs: the private original in settings-kv.

| What happened | Lesson | Rule for TradePro |
|---|---|---|
| Wednesday's SMH hedge was bought in a sell-off; Friday's decisions, made on a green day, got better prices | Decide on calm or green days, not in a panic | No new hedges after a −5% day; reprice the next calm session |
| A strong gap-up open faded within minutes on the half day | The first 15 minutes are the noisiest | No proposals before open + 15 min; limits near mid |
| A name bounced 19% in a week, so its naked put closed for a small loss | Recoveries are the cheap exit for naked puts | Repair check: when a naked put is back near breakeven, propose closing |
| A naked put before earnings: closing (a known loss) vs a floor (a coin flip of similar size) | Before earnings, take the known loss unless there is a real bullish view | Earnings-in-expiry: default close; floor only with a stated view (owner basics 3) |
| Calls sold into strength with a wide cushion; a fading name skipped | Sell covered calls into strength and leave recovery room | Covered calls only on up days, ~0.30 delta, strike well above the recovery level |
| Jan SMH put spread not bought because the Dec package still protects | Don't stack hedges on the same risk | Size hedges to remaining exposure; shrink them as naked puts are cleared |
| Selling a call spread to fund the SMH puts doubled the loss in the rally | A short call in a hedge is a second bet, not insurance | Hedges are plain put spreads, with no short call leg |
| The index broke support after the spread was sold; the exit level was already set | Set the exit before entering | Every proposal carries a written stop and target |
| Unrealised P&L (against entry prices) read as a large loss while net liquidation was near its high | Unrealised P&L is against entry prices, not deposits | Progress, the floor and targets are measured on net liquidation only |
| Most of the damage came from naked short puts | Naked short options carry unlimited downside | Every new short option is a spread or a covered call — never naked |

## Account guardrails in force (for reference)

- Net liquidation floor and the quarterly milestones: settings-kv
  `risk_guardrails` (owner's figures, not in the repo).
- Max loss per income trade: $3k. Put-spread credit ≥ ⅓ of width.
- No new bullish bets while the book is heavily net long one sector
  (owner basics 5: 40–45% sector cap).
- No £100–200 payoff trades.
