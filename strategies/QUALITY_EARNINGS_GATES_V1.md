# Quality + earnings drop — PRE-REGISTERED gates

**Committed BEFORE the run**, 29 Aug 2026.

## The owner's claim, third and best-specified attempt

> "fundamentally strong stocks falling after result and getting back fine"
> ...and, on what fundamental means: "the earning ratio, p/e ratio etc"

Two earlier tests missed it and he was right both times:
1. `512f5af` ran earnings drops through the SWING exit (-8% stop, 20-session
   cap) — anything recovering over months was stopped out first. Wrong machinery.
2. `a50dea8` fixed the horizon: drops DO recover (+17% at 120 sessions, 65%
   positive) but UNDERPERFORM the same stock bought on a random day by 7.1pt.

Neither applied a quality filter, because I did not have the data. Now I do.

## Quality, defined from what actually exists

Point-in-time only. Today's P/E stamped on a 2023 event is look-ahead and would
manufacture an edge from nothing.

    profitable   most recently REPORTED annual diluted EPS > 0
    growing      that EPS > the prior year's
    priced       point-in-time P/E = close / trailing reported EPS, in [5, 60]

QUALITY = all three. Anything failing one is the comparison arm.

## Gates

| # | Test | Threshold |
|---|------|-----------|
| **V0** | Quality drop events | >= 150 |
| **Q1** | Quality drops BEAT their own null | > 0 at 60 AND 120 sessions |
| **Q2** | Quality beats NON-quality drops | by >= 1.0pt at 120 |
| **Q3** | Edge survives the TWO-SPLIT | positive in all four cells (time x symbol) |

**Q1 is the gate that matters.** The null is the SAME STOCK on a random
non-earnings day above its 200-SMA, so stock quality is present on BOTH sides
and largely cancels. The question is not "do quality stocks go up" — they did,
it was a bull market — but "does the earnings DROP add anything, within a
quality name".

## COVERAGE LIMIT, carried with every number

Exactly **4 annual EPS points per symbol**, earliest 2022 (172 names) or 2023
(33). A point-in-time P/E needs one reported figure before it, so the usable
window is roughly **2023-2026: three years of one post-COVID bull regime**.

The asymmetry is the reason to run it anyway. A PASS here is weak evidence. A
FAILURE is strong, because these are the friendliest conditions the hypothesis
will ever be given — quality names, rising market, no stop, months of runway.

## Prediction — recorded before the work

**I expect Q1 to FAIL**, and the reason is the null, not the hypothesis. The
null is drawn from the SAME stock, so "quality recovers" is already inside both
arms. Filtering to quality changes which stocks are in the sample; it does not
change whether the earnings drop itself is a better entry than an ordinary day
in that same stock. My earlier -7.1pt was measured with that control already in
place, and I do not expect a quality filter to reverse it.

**Where I could be wrong, stated honestly**: if quality names OVERREACT less and
mean-revert faster specifically after earnings, the effect would be real and
would show up as Q1 passing at 60-120 sessions while the unfiltered version
failed. That is a documented effect in the literature and it is exactly what the
owner is describing. If it appears, he is right and I have been answering a
coarser question than he was asking for three days.

**I will not move these gates after seeing the numbers.**
