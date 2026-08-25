# Wheel on INDEX ETFs — a different bet, its own gates. Pre-registered.

**Written BEFORE the run, 25 Aug 2026.** Owner: *"if index etf are wheel
candidates we could try that — no harm in doing that."*

## Why this is a new strategy and not wheel v5

Three attempts on single stocks have failed, and the harness has been explicit
about why: *"entry filters cannot reach the failure mechanism, which is being
assigned into a decliner and holding it."* v3 filtered entries; v4 stopped the
assigned shares and made returns worse while G4 moved non-monotonically.

**The failure is not a rule, it is the universe.** A single company can fall
71% and never recover — META did, and TSLA fell 57%. Premium of 6–8% a year
cannot pay for that.

**This is confirmed live in the owner's own account, today.** He runs this
wheel by hand and it has realised roughly **+$3,346 in seven weeks**, including
a textbook ACN cycle (assigned 130.44 → called away 145, +$1,944). And it holds
**APLD, assigned at 38.60, now 28.64 — down $997**, with a 38-strike covered
call earning $0.35 that cannot repair it. Both halves of the backtest, in one
account: the wheel works on names that recover, and bleeds on the one that did
not.

An index cannot do what APLD did. The worst modern drawdown on a broad US index
is roughly −55% (2008) and it recovered. **That is the whole thesis of this
variant**: keep the mechanism, change what you can be assigned into.

## Universe

Broad, liquid, optionable index ETFs only — no sector funds, no single
countries, no leveraged or inverse products, no commodity trusts.

    SPY  QQQ  IWM  DIA  VTI  VOO  IVV  EFA  VXUS

Deliberately NOT included: XLF/XLE/XLK and the rest of the sector complex,
which can and do behave like concentrated single bets; SLV and GLD, which have
no earnings but also no fundamental floor.

## Gates — the SAME bar as the single-stock wheel

Not relaxed. If the ETF variant cannot clear the bar the stock version was
held to, the honest answer is that the wheel does not earn its capital here
either.

| # | test |
|---|---|
| G3 | full-period net CAGR ≥ 8%/yr |
| G4 | worst single-symbol drawdown ≤ 40% |
| plus the remaining v3 gates as the harness scores them |

Modelled with the same live frictions already in the harness: premium haircut,
per-leg commission, the premium floor, and idle cash earning the bank rate.

## Prediction, written before the run

**G4 will pass comfortably.** This is close to definitional — the worst
single-symbol drawdown is bounded by what a broad index can do, and even 2008
is inside 40% for most of these once premium is netted. If G4 fails, something
is wrong with the harness, not with the thesis.

**G3 is where this lives or dies, and I genuinely do not know.** The trade is
explicit: ETF implied volatility is far lower than APLD's, so the premium is
thinner. My instinct is that it lands in the **4–6%** range — better than cash,
short of the 8% gate, and therefore a FAIL by the letter of the rule.

**What would change my mind:** if idle cash at the bank rate plus a lower
assignment rate carries it above 8%. An ETF wheel gets assigned less often, and
every month not assigned is a month of clean premium. That effect is real and I
have not sized it, which is the main reason this is worth running rather than
reasoning about.

**If G3 comes in between 6% and 8%**, that is the interesting outcome and the
one I will NOT quietly round up. It would mean the ETF wheel is a real,
low-drawdown, sub-8% strategy — and the decision then belongs to the owner:
whether an 8% bar is the right bar for a sleeve whose worst case is −25% rather
than −71%. That is a decision to make deliberately and in advance of the next
run, not a gate to relax after seeing the number.
