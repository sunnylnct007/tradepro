# Size limits replay for the put book — gates, v1

Status: PRE-REGISTERED 28 Nov 2026. Not run. Written before any strike,
expiry, daily cash or obligation series was assembled, so the bars are fixed
before the data is seen. To be run by the build agent once item 16's trade
journal (strikes, expiries, premiums from Flex) exists.

## Why
`SECTOR_TREND_GATE_RESULT_V1.md` and `STOCK_TREND_GATE_RESULT_V2.md` both
failed: trend rules cannot flag a put sold on a stock still near its highs.
The operating model's answer is size limits. This tests whether those limits
would actually have contained autumn 2026, and what they would have cost in
the profitable summer.

## Replay (fixed)
Walk every opening short-put sale on a single stock, 1 Jun – 27 Nov 2026,
in time order, against a **replayed** book (a refused entry never enters
it). Refuse the entry if any rule fails at that moment:

- **R1 cash coverage:** Σ strike × 100 of open short puts, including the
  new one, ≤ cash (USD equivalent, from the Flex daily cash report) − $50k.
- **R2 CSP cap:** at most 2 open naked or cash-secured short puts.
- **R3 sector cap:** the sector's option-adjusted exposure after the trade
  ≤ 45% of net liquidation (segment map of item 1).

Two books are produced:
- **A — refuse:** refused entries are simply not opened.
- **B — convert:** a refused entry is opened as a put spread instead (long
  put ~10% of spot below the short strike, same expiry), if its max loss is
  ≤ $3k and the income book's total max loss stays ≤ £12k; otherwise refused.

Option marks: harvested chains where they exist; otherwise Black–Scholes
from the underlying's close and 30-day realised volatility, labelled as
modelled. "Bad" entries are the 24 defined in the v1 result.

## Gates
- **G1 contains the damage:** in book A, the put book's worst
  mark-to-market loss between 1 Oct and 27 Nov is ≤ $15k. (Reference: about
  $100k to close on 5 Nov in the real book.)
- **G2 caps the obligation:** in book A, peak Σ strike × 100 is ≤ $150k.
  (Reference: about $570k on 5 Nov.)
- **G3 the spreads hold:** in book B, the converted spreads' combined
  realised and open loss between 1 Oct and 27 Nov is ≤ £12k, and no single
  spread loses more than $3k.
- **G4 the income survives:** book B's net premium kept from entries opened
  9 Jul – 30 Sep is ≥ 40% of the real book's for the same entries.

PASS only if all four hold. Also report, ungated: how many of the 24 bad
entries each rule refused, which rule bound first on each date, and net
liquidation path of the real book vs books A and B.

## Prediction (recorded before the run)
G1 and G2 pass easily — R2 alone refuses most entries, good and bad, which
is why they are weak evidence on their own. The real questions are G3
(whether 10%-wide spreads on names that fell 20–50% all hit max loss
together and breach the £12k cap) and G4 (whether spread credits at ⅓ of
width were available on the summer entries at all). Expect G3 to be tight
and G4 to fail on low-volatility names.
