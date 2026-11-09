"""When to close a short put — stated rules, not opinions.

Owner, 1 Nov 2026: "i need indications on puts i shd be closing", of a LIVE
book of short puts.

EVERY VERDICT NAMES THE RULE AND THE NUMBER THAT TRIGGERED IT. This is real
money and the screen does not get to say "close" without showing its working;
the owner has to be able to disagree with the rule rather than with a verdict
that appeared from nowhere.

WHAT THIS MODULE GOT WRONG UNTIL 8 Nov 2026, and why it is rewritten:

The old BREACH rule closed a position when the mark reached 2x the credit. The
owner rejected it by name:

    "see thing like CLOSE PLTR 165P 01-15 68d -$938 costs 2.3x what was
     collected (7.14 -> 16.52) - past the 2x line.  is not a real evaluation"

He was right, and the arithmetic says so. PLTR: strike 165, credit 7.14, so
the effective basis is 157.86 while the stock was 160.38. Assignment would
have handed him PLTR BELOW the market. The rule called that CLOSE because the
OPTION had lost money — but the option's mark and the trade's outcome are
different statements, and conflating them flagged two thirds of the book
when a third of it was outright winning.

The mark of a short option is not a loss. It is what the position would cost to
exit TODAY, and most of it is time value that decays in the seller's favour.
Measured on the live book that day, the mark was THREE TIMES the loss the
same positions carried held to expiry at the SAME prices. The screen had been
overstating the damage by that factor for weeks.

So the rules no longer ask "has the premium moved against us". They ask the two
questions a premium seller actually faces:

  1. WOULD I OWN THE STOCK AT THE EFFECTIVE BASIS? Short a put is an offer to
     buy at strike-minus-credit. If the stock trades above that, assignment is
     profitable and the option's mark is irrelevant.
  2. WHAT DOES CLOSING COST ME IN DECAY I HAVE ALREADY EARNED? Buying back a
     put whose mark is nearly all time value forfeits premium that would
     otherwise accrue. Every row now states that number, because the owner's
     own damage-control plan (exit a position once it recovers) cost several
     thousand against simply letting it expire - and nothing said so.

THE RULES, and why each one is here:

  TARGET      >= `take_profit_pct` of the credit is already captured. The
              remaining premium is the smallest part of the trade and it
              carries the same assignment risk as the first half. Standard
              premium-selling discipline, and the one rule here that is about
              opportunity rather than danger.

  ASSIGNMENT  in the money with `near_expiry_days` or fewer to run. Time has
              stopped being a defence and a decision is being forced. NOTE
              this fires whether or not the basis is favourable - what it
              reports is "shares are about to arrive", which the owner may
              well want. The verdict says which case it is.

  BASIS       the stock is more than `max_basis_shortfall_pct` below the
              effective basis. THIS is the real "the thesis is wrong" rule
              that BREACH was reaching for and missing: it means assignment
              would buy stock materially above the market, which is the only
              way a cash-secured put actually loses money.

  WATCH       in the money, but the basis still holds or is only slightly
              under. Assignment here is acceptable-to-good.

  HOLD        out of the money and running. Doing nothing is a position.

WHAT THIS DELIBERATELY DOES NOT DO: size, roll, or tell anyone what to buy.
It reports the state of a position against rules the owner can read, and the
decision stays his.
"""
from __future__ import annotations

from dataclasses import dataclass

from .economics import cash_secured_put


@dataclass(frozen=True)
class ManageRules:
    """Thresholds, all config-driven and all conventional rather than tuned.

    50% of credit is the long-standing premium-seller convention. 21 days is
    where gamma begins to dominate a short option — the same boundary the
    screen uses at the other end when it picks an expiry.

    10% below basis is a judgement, and it replaced a 2x-the-credit rule that
    measured the wrong thing entirely (see the module docstring). It is stated
    as a distance from the price at which the trade breaks even, because that
    is the number that decides whether assignment hurts. Ordinary noise does
    not move a stock 10% through a basis; a broken thesis does.
    """
    take_profit_pct: float = 50.0
    near_expiry_days: int = 21
    max_basis_shortfall_pct: float = 10.0


@dataclass(frozen=True)
class Verdict:
    """A verdict AND the arithmetic behind it.

    The numeric fields are here so a caller never has to re-derive them and
    risk deriving them differently — the duplicate-definition shape that has
    cost this desk more time than any other single bug.
    """
    action: str              # CLOSE | WATCH | HOLD | UNKNOWN
    rule: str                # which rule fired — "" when none did
    why: str                 # the number that fired it
    urgency: int             # 0 none, 1 watch, 2 act — for sorting a board
    effective_basis: float | None = None   # what assignment really costs/share
    basis_cushion_pct: float | None = None # +ve = stock above basis = good
    close_pnl: float | None = None         # $ booked by exiting at the mark now
    hold_pnl: float | None = None          # $ at expiry IF the stock stays put
    forfeit: float | None = None           # hold_pnl - close_pnl: decay given up


def _pct_captured(credit: float, mark: float) -> float:
    """How much of the premium sold has been earned back, as a percent.

    Credit is what we received per share; mark is what it would cost to buy
    back now. Both per-share, so the multiplier cancels.
    """
    if credit <= 0:
        return 0.0
    return (credit - mark) / credit * 100.0


def assess(*, strike: float | None, spot: float | None, credit: float | None,
           mark: float | None, dte: int | None,
           rules: ManageRules | None = None) -> Verdict:
    """One short put, one verdict.

    MISSING INPUTS RETURN UNKNOWN, never HOLD. "We could not assess this" and
    "this is fine" are different statements, and a book where the unreadable
    rows look settled is exactly how a position gets forgotten.
    """
    r = rules or ManageRules()
    if strike is None or spot is None or credit is None or mark is None:
        missing = [n for n, v in (("strike", strike), ("spot", spot),
                                  ("credit", credit), ("mark", mark)) if v is None]
        return Verdict("UNKNOWN", "", f"cannot assess — no {', '.join(missing)}", 1)

    # EFFECTIVE BASIS COMES FROM economics.cash_secured_put, not from a local
    # `strike - credit`. It is the same quantity the screen already calls the
    # breakeven, and there is now exactly one place that decides what it means.
    basis = cash_secured_put(strike=strike, credit_per_share=credit).breakeven

    itm = spot < strike
    depth = (strike - spot) / strike * 100.0 if strike else 0.0
    captured = _pct_captured(credit, mark)
    cushion = (spot / basis - 1.0) * 100.0 if basis > 0 else 0.0

    # What the two choices are actually worth, per contract.
    #
    # close_pnl  exit now at the mark.
    # hold_pnl   let it run to expiry WITH THE STOCK WHERE IT IS TODAY. Not a
    #            forecast — it is the same no-change assumption, carried to
    #            expiry instead of stopped at the mark, and it is the honest
    #            counterfactual to closing.
    # forfeit    the difference, which is the time value being handed back.
    intrinsic = max(0.0, strike - spot)
    close_pnl = (credit - mark) * 100.0
    hold_pnl = (credit - intrinsic) * 100.0
    forfeit = hold_pnl - close_pnl

    def v(action: str, rule: str, why: str, urgency: int) -> Verdict:
        return Verdict(action, rule, why, urgency,
                       effective_basis=basis, basis_cushion_pct=cushion,
                       close_pnl=close_pnl, hold_pnl=hold_pnl, forfeit=forfeit)

    # Said on every CLOSE verdict, because a verdict that recommends paying
    # for decay should name the price of that.
    cost = (f" Exiting now books {close_pnl:,.0f} and forfeits {forfeit:,.0f} "
            f"of time value — held to expiry at today's price it is "
            f"{hold_pnl:+,.0f}.") if forfeit > 1.0 else ""

    # TARGET first: it is the only rule that is pure opportunity, and when it
    # fires there is nothing to weigh — the premium has been earned.
    if captured >= r.take_profit_pct:
        return v("CLOSE", "target",
                 f"{captured:.0f}% of the credit captured ({credit:.2f} sold, "
                 f"{mark:.2f} to buy back) — the rest carries the same risk for "
                 f"less reward", 2)

    # ASSIGNMENT next: the only rule with a deadline attached. It says shares
    # are coming, and then says whether that is good news, because on this
    # book it very often is.
    if itm and dte is not None and dte <= r.near_expiry_days:
        if cushion >= 0:
            return v("WATCH", "assignment",
                     f"{depth:.1f}% in the money with {dte}d left, so "
                     f"assignment is likely — but the basis is {basis:.2f} "
                     f"against {spot:.2f}, so the shares arrive "
                     f"{cushion:.1f}% BELOW the market. Profitable if taken.", 1)
        return v("CLOSE", "assignment",
                 f"{depth:.1f}% in the money with {dte}d left — time has "
                 f"stopped being a defence, and the basis of {basis:.2f} is "
                 f"{-cushion:.1f}% above the {spot:.2f} market, so assignment "
                 f"buys {strike * 100:,.0f} of stock at a loss.{cost}", 2)

    # BASIS: the thesis is wrong by the only measure that costs money.
    if cushion < -r.max_basis_shortfall_pct:
        return v("CLOSE", "basis",
                 f"the stock is {-cushion:.1f}% below the {basis:.2f} basis "
                 f"({spot:.2f} now) — assignment would buy above the market by "
                 f"more than the {r.max_basis_shortfall_pct:.0f}% line.{cost}", 2)

    if itm:
        return v("WATCH", "in the money",
                 f"{depth:.1f}% in the money"
                 + (f" with {dte}d to recover" if dte is not None else "")
                 + (f", and the {basis:.2f} basis still holds — "
                    f"{cushion:.1f}% of cushion to the {spot:.2f} market"
                    if cushion >= 0 else
                    f", {-cushion:.1f}% under the {basis:.2f} basis but inside "
                    f"the {r.max_basis_shortfall_pct:.0f}% line"), 1)

    return v("HOLD", "", "out of the money"
             + (f" by {-depth:.1f}%" if depth < 0 else "")
             + (f", {dte}d left" if dte is not None else "")
             + f"; basis {basis:.2f} vs {spot:.2f}", 0)


# ── reading a contract out of its symbol ──────────────────────────────────

def parse_occ(symbol: str) -> tuple[str, str, str, float] | None:
    """OCC option symbol -> (root, expiry YYYY-MM-DD, right, strike).

    IBKR's Flex reports name a contract the way the exchange does:

        "AMD   270115P00450000"
         root  YYMMDD R strike x 1000, zero-padded to 8

    PARSED RATHER THAN SPLIT ON WHITESPACE. The root is padded to six
    characters, so a six-letter root leaves NO space before the date and a
    naive split silently yields the wrong field. The date-strike block is a
    fixed 15 characters at the END, which is the only part of the format that
    is actually guaranteed.

    THE STRIKE DIVISOR IS THE EASY ONE TO GET WRONG: 00450000 is $450.000, not
    $450,000. A risk screen that reports a strike a thousand times too large
    would make every assignment number absurd — and absurd numbers get
    dismissed rather than investigated.

    Returns None for anything that is not an option symbol, so a stock row
    falls through instead of being coerced into a contract.
    """
    s = (symbol or "").strip()
    if len(s) < 16:
        return None
    tail = s[-15:]
    root = s[:-15].strip()
    if not root or not tail[:6].isdigit() or not tail[7:].isdigit():
        return None
    right = tail[6].upper()
    if right not in ("C", "P"):
        return None
    yy, mm, dd = int(tail[0:2]), int(tail[2:4]), int(tail[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    try:
        strike = int(tail[7:]) / 1000.0
    except ValueError:
        return None
    return root, f"20{yy:02d}-{mm:02d}-{dd:02d}", right, strike
