# RISK_GUARDRAILS_V1 — risk guardrails, position repair and hedging (spec)

Pre-registered 25 Nov 2026 from the owner's spec (written with another agent).
Account figures, the owner's positions, fills and legs are NOT in this file:
the account floor and the live book live in settings-kv and the IBKR Flex read,
never in the public repo. Open decisions are listed at the end; nothing below a
decision is built until the owner answers it.

## 1. Purpose and scope

TradePro flags **repair options** on the live IBKR book every day, not just
"close". The lesson of 25 Nov 2026 (semis sold off hard intraday and reversed):
most of the damage came from positions with no defined loss — naked short puts
and uncovered shares — and the repair was done late and by hand. This module
turns those decisions into standing rules, checks and alerts.

In scope: the live IBKR account (READ-ONLY, Flex / connector), option-based
repair and hedging, intraday shorts, monitoring. Out of scope: order execution
(the owner submits every order), index strangles, paper strategies.

TradePro never places an order on the live account. Every output is a proposal
with a ready ticket; the owner decides.

## 2. Account guardrails

Every proposal passes these before it is shown. One that breaks a rule is shown
as BLOCKED with the rule named — never hidden.

| # | Rule | Value | Check |
|---|------|-------|-------|
| G1 | New short options are spreads only | no naked puts or calls | every short leg has a long leg, same expiry |
| G2 | Put-spread width | long put ≈10% of spot below the short strike | width / spot between 8% and 12% |
| G3 | Minimum credit on credit spreads | credit ≥ 1/3 of width | reject below |
| G4 | Max loss per income trade | `income_engine.max_loss_usd` (settings-kv) | width − credit, or debit paid |
| G5 | Account floor | `risk_guardrails.account_floor_gbp` (settings-kv) | worst-case net liq after the trade, all floors applied |
| G6 | Hedge before repair | sector/index hedge first, single-name floors second | flag unhedged beta-adjusted exposure |
| G7 | Calls only into strength | sell calls after a bounce, never at the day's low | §5 |
| G8 | Don't give upside away cheaply | premium ≥ `min_call_premium_pct_per_month` of spot | §5, open decision |
| G9 | No closing winners for % captured alone | close only if the freed capital goes into a new trade | existing owner rule |

G4 applies to income trades. Portfolio hedges (§4) may exceed it with the
owner's explicit approval per trade.

## 3. Position repair check

Runs daily before the US open (14:00 London) and again intraday on any −3%
NQ move. For each flagged position show every repair option with its cost and
new worst case side by side; "close" is one row among them.

Triggers:

| Position | Flag when | Why |
|----------|-----------|-----|
| Naked short put | spot within `naked_put_flag_pct` (5%, open decision) of strike, or ITM | repair is cheapest early |
| Shares with no call | spot > 10% below cost basis | collar / put spread / call on a bounce |
| Short call | ≥50% captured and the stock fell ≥10% | roll-down candidate |
| Short option | ≤21 DTE and ITM | roll / assignment decision due |

Repair menu per flag (live mids, cost, new max loss, effect on worst-case net liq):

1. **Floor** — buy a lower put, same expiry, budget = premium collected (+ any call-spread credit). Naked put → put spread.
2. **Call-spread funding** — sell a ~5% OTM call spread on the same name to pay for the floor. Defined risk only; never a naked call against a short put.
3. **Collar (shares)** — sell an OTM call, buy an OTM put; show the cap vs cost basis.
4. **Protective put spread (shares, no call)** — full upside kept, a band protected.
5. **Call roll-down** — buy back the winning call, sell a closer one on a bounce.
6. **Stock repair** — buy 1 call, sell 2 higher calls at ~zero cost, covered by shares.
7. **Close** — realised loss shown, for comparison only.

Output per position: current loss, uncapped worst case, then one row per
option: cost, new worst case, upside given up. Ranked by worst-case reduction
per dollar spent.

## 4. Portfolio hedge module

Hedge the market move at SECTOR level first: one sector-ETF package covers the
whole segment and costs less per dollar of protection than single-name floors.

Standard package: buy a put spread ~2% / ~12% OTM; sell a call spread ~4% / ~9%
OTM to fund it (both legs bearish, cost ≈ half the put spread alone); 2–4 weeks
to expiry; volatility-neutral.

Sizing: exposure = shares + deep-ITM short puts as share-equivalents (Δ≈1), in
USD, mapped to the hedge ETF; beta to the sector ETF per name (memory/semis
≈1.5–2×); default cover `hedge_cover_pct` (25% of the loss from a −10% sector
move, open decision) — show units for 25% / 40% / 50% with cost if flat, payout
at −10% and at the long strike, worst case if the sector rallies past the
short call.

Add / remove triggers:

| Event | Action |
|-------|--------|
| sector ETF breaks the day's low or a named support | propose adding units |
| a bounce makes the package ≥30% cheaper than the last fill | propose adding units |
| daily close above the short-call strike area | stop adding; hold |
| sector at or below the short-put strike | propose taking profit on half |
| 5 trading days before expiry | FORCE CLOSE (ETF options cannot be held into exercise on this account) |

Scale in: half now, half on a bounce or a support break.

## 5. Covered call manager

Sell calls into strength. Rules:

1. Bounce trigger per name: a level a few percent above the day's low; alert with a ready ticket when hit.
2. Minimum premium ≥ `min_call_premium_pct_per_month` of spot (2% or 3%, open decision).
3. Strike 8–18% above spot by default; never below the owner's stated cap preference for the name.
4. Keep part uncovered: ≥ ~300 shares or ~40% of a large holding stays free.
5. Roll-down: when a short call has dropped ≥50% after a fall, buy it back on the down day, sell the new closer call on the next bounce.
6. Pre-holiday / last-session fallback: no bounce by 20:15 London → propose a smaller starter sale.
7. Every call proposal states the cap vs cost basis.

Strike and management: sell covered calls at ~0.30 delta (IBKR greeks), plain,
never as a spread (the shares cover them). Roll up and out when delta > ~0.50
or spot is within ~5% of the strike, for even money or a small credit. Never
let a call be assigned below cost unless the owner has chosen to exit. Spreads
are for calls with no shares behind them.

Per-name choices when a large unrealised loss exists: A roll down to a single
call (flat or falling); B stock repair 1×2 (recovery expected); C no call +
protective put spread (recovery expected, downside cover wanted).

## 6. Intraday short rules

| Item | Rule |
|------|------|
| Setup | below session VWAP with lower highs since the open; enter on a failed bounce to VWAP |
| Entry | limit at or just under VWAP; never chase below the current price |
| Stop | just above the high of the day; never widened |
| Target | extension below the day's low, R:R ≥ 1:1.3 |
| Size | max loss $500–1,000; payoff must exceed £500 |
| Margin | check available funds first; no stacking shorts |
| Exit | flat by 20:45 London |
| Blackout | no new intraday shorts after a −5% day for the stock going into a holiday or the week's last session |
| Alternative | prefer a short-dated put debit spread when a squeeze is likely |

## 7. Monitoring and alerts

Alert only when there is something to act on, with a ready ticket; otherwise
one status line.

| Check | London | What |
|-------|--------|------|
| Pre-open | 14:00 | futures vs overnight range, net liq vs floor, repair flags |
| Open + 5 | 14:35 | live quotes for anything queued; nothing before 14:30 |
| Intraday | 16:00, 18:00 | bounce triggers, floor-price drops ≥15%, hedge triggers, open intraday positions + stops |
| Pre-close | 20:15 | flatten intraday shorts by 20:45, final call tickets, USD cash debit |
| After a holiday | open + 15 min | what the gap did; reprice floors and hedges |

Alert contents: trigger, live bid/ask/mid, the ticket (strikes, qty, limit),
max loss, which guardrail it satisfies. Price sanity: a quote with `is_close`
true or bid/ask wider than 20% of mid is STALE — never size off it.

## 8. Broker and data constraints (each caused a wrong or blocked proposal)

| Constraint | Effect | Handling |
|-----------|--------|----------|
| MiFID retail (PRIIPs) | US ETF shares cannot be traded | ETF options or single stocks; never propose ETF shares |
| ETF options and exercise | exercise into ETF shares may be blocked | force close ≥5 trading days before expiry |
| connector 4-leg combo | instruction fails | two 2-leg spreads, put side first |
| connector instructions | cannot be edited/deleted; expire after 7 days | new instruction per price change; name the old ones to ignore by limit |
| order status "Replaced" | bracket children look inactive via the connector | the owner's Orders screen is truth |
| combo labels | "Bull Call" sold = bear call spread | always state legs explicitly (buy X / sell Y) |
| pre-open option quotes | wide, often stale | no option pricing before 14:30 London |
| USD cash | no auto-conversion; option buys create a USD debit | flag USD < 0 with a conversion amount |
| paper vs live | TradePro's IBKR tools read the paper layer | the repair check reads the LIVE book (Flex / connector) |

## 9. Defined-risk options sleeve

Options trading in TradePro is built on spreads: every trade's maximum loss is
known before it is placed. This replaces naked put-selling as the core options
strategy.

Structures to scan for: bull put spread (quality name after a drop); bear call
spread (after a sharp rally, or to fund a floor); bear put spread (hedge /
directional); put spread + call spread package (index hedge at low net cost);
covered call (shares held, sold into strength); protective put spread on
shares; floor on an existing short put.

Rules: G3/G4; sell premium when IV is high, buy protection when IV is low;
short strikes ~0.20–0.30 delta, long strikes ~10% of spot beyond; 3–7 weeks to
expiry for income, close or roll at ≤5 trading days; no new spreads with
earnings inside the expiry unless deliberately an earnings trade; every
proposal shows max gain, max loss, breakeven, probability (delta), effect on
worst-case net liq vs the floor. Target: steady income (~10%/yr on allocated
capital, the 23 Nov goal) with no single trade able to create a large loss.

## 10. Standing hedge map

A small hedge stays on permanently per segment of the book, in the index that
moves with it, topped up when protection is cheap. Segments are defined by
sector membership; the names, exposures and current hedges are read from the
live book daily, never written here.

| Segment | Hedge instrument |
|---------|------------------|
| Semis / memory / AI hardware | SMH put spread (+ call spread to fund) |
| Software / cloud | IGV put spread |
| Mega-cap / broad tech | QQQ put spread |
| Healthcare | none unless exposure grows |

Rules: recompute segment exposure daily (shares + deep-ITM short puts as
share-equivalents, beta-adjusted); target cover ~25% of a −10% segment move,
propose top-ups below 15%; top up when IV percentile < 50% or after a calm
week, avoid buying after a >5% segment drop unless cover is below 10%; roll
~2 weeks before expiry; never hold ETF options into exercise; report hedge
P&L beside book P&L so a losing hedge on a rally day is read correctly.

## 11. Open decisions (owner)

- [ ] Repair-check trigger for naked puts: within 5% of strike, or only once ITM?
- [ ] Minimum call premium: 2% or 3% of spot per month?
- [ ] Hedge budget: does G4 apply to portfolio hedges, or a separate monthly hedge budget?
- [ ] Default hedge cover: 25% of a −10% sector move, or higher?
- [ ] Should G5 block new trades outright, or only warn?
- [ ] Live book source: IBKR Flex nightly (T+1), or the connector intraday?
- [ ] Build order: §3 repair check (read + flags) → §4 hedge sizing → §5 call manager → §7 alerts; §6 intraday shorts are a scorecard first.
