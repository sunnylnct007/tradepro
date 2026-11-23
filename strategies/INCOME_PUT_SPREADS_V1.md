# INCOME_PUT_SPREADS_V1 — pre-registration (owner, 23 Nov 2026)

The owner traded this by hand on 23 Nov 2026 and expected TradePro to have
flagged it. This file freezes the rules as the owner stated them, BEFORE any
screen is built or any record graded. Settings live in settings-kv under
`income_put_spreads` (config-driven, editable from the UI); the values below
are the registered defaults.

## What the trade is

A bull put spread (sell a put, buy a lower put, same expiry) on a quality,
dividend-paying, non-tech single name or an index, sized so the maximum
loss is a fixed cash amount. Income, not direction: the thesis is that the
short strike is below where the stock has recently traded and nothing is
scheduled to break that.

## The engine's settings (registered defaults)

| setting | value |
|---|---|
| capital allocated to income | £100,000 |
| target | 10% a year on that capital |
| maximum loss per trade | £3,000 |
| maximum loss per sector (open trades) | £6,000 |
| mix | 50% indices, 50% quality single names |
| excluded for now | semiconductors, memory |
| total risk on NEW trades | capped at £6,000 until the legacy short puts have halved |

## The gates, in the order the owner applied them

1. **Universe** — sector, dividend payer, liquid options, not already in the
   book (no name that duplicates an existing position or the rotation /
   pullback sleeves).
2. **Earnings** — skip if an earnings date falls before expiry (source:
   `catalysts`, the events calendar).
3. **Technical** — uptrend only (above the 200-day, above the cloud), RSI and
   Bollinger not stretched; the short strike BELOW the 30-day low.
4. **Probability** — chance the spread expires worthless from implied vol;
   chance of max loss; IV against realised movement.
5. **Sizing** — contracts = £3,000 ÷ ((width − credit) × 100), then the
   £6,000 sector cap and the £6,000 new-trade cap.
6. **Order** — the combo order written out for the owner to approve. TradePro
   never places it: live is READ-ONLY + email; any placement is paper only.
7. **Monitor** — daily: take profit at 50% of the credit; exit on a CLOSE
   below the swing low. Alerts, not orders.

## What counts as a result

This is an evaluation, like every other strategy here: the screen's
candidates and the owner's manual trades go into ONE forward record, graded
only when a trade closes or expires. Marks to beat, registered now:

- credit kept ≥ 65% of trades (max-profit or 50% take-profit), n ≥ 30
- realised loss on a losing trade ≤ the registered maximum (sizing holds)
- annualised return on the £100k ≥ 10% over a rolling year, drawdown ≤ 10%

A screen that cannot compute a gate SAYS so and refuses the candidate; it
never shows a row it cannot support.

## Build order (depth before breadth)

1. The screen (gates 1–5) as a Lambda job publishing `today-setups/income_put_spreads`, with every gate's number shown per row.
2. The forward record: the owner's manual trades entered as fills, marked daily from the live account READ-ONLY.
3. The monitor (gate 7) as alerts in the existing trade-alerts lane.
4. The order instruction (gate 6) last.
