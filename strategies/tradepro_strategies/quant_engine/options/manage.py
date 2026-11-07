"""When to close a short put — stated rules, not opinions.

Owner, 1 Nov 2026: "i need indications on puts i shd be closing", of a LIVE
book of 22 short puts sitting at -$20,233 inside an account with £36,911 of
excess liquidity against a £168,967 maintenance requirement.

EVERY VERDICT NAMES THE RULE AND THE NUMBER THAT TRIGGERED IT. This is real
money and the screen does not get to say "close" without showing its working;
the owner has to be able to disagree with the rule rather than with a verdict
that appeared from nowhere.

THE RULES, and why each one is here:

  TARGET      >= `take_profit_pct` of the credit is already captured. The
              remaining premium is the smallest part of the trade and it
              carries the same assignment risk as the first half. Standard
              premium-selling discipline, and the one rule here that is about
              opportunity rather than danger.

  ASSIGNMENT  in the money with `near_expiry_days` or fewer to run. Time has
              stopped being a defence. This is the rule that matters most on
              this book: MRVL Nov20 230P is 14.6% in the money with 19 days
              left and would put $23,000 of stock into an account with
              £36,911 of excess liquidity.

  BREACH      the position has lost `loss_multiple_of_credit` times what was
              collected. The thesis is wrong by a margin that selling more
              premium will not repair.

  WATCH       in the money, but with time left to recover.

  HOLD        out of the money and running. Doing nothing is a position.

WHAT THIS DELIBERATELY DOES NOT DO: size, roll, or tell anyone what to buy.
It reports the state of a position against rules the owner can read, and the
decision stays his.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManageRules:
    """Thresholds, all config-driven and all conventional rather than tuned.

    50% of credit is the long-standing premium-seller convention. 21 days is
    where gamma begins to dominate a short option — the same boundary the
    screen uses at the other end when it picks an expiry. 2x credit is a
    judgement: it is far enough out that ordinary noise does not trip it, and
    close enough that a thesis which has broken is named before it compounds.
    """
    take_profit_pct: float = 50.0
    near_expiry_days: int = 21
    loss_multiple_of_credit: float = 2.0


@dataclass(frozen=True)
class Verdict:
    action: str          # CLOSE | WATCH | HOLD | UNKNOWN
    rule: str            # which rule fired — "" when none did
    why: str             # the number that fired it
    urgency: int         # 0 none, 1 watch, 2 act — for sorting a board


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

    itm = spot < strike
    depth = (strike - spot) / strike * 100.0 if strike else 0.0
    captured = _pct_captured(credit, mark)

    # ASSIGNMENT first: it is the only rule with a deadline attached, and a
    # position can satisfy several at once. Ordered by what forces a decision
    # soonest rather than by what looks worst.
    if itm and dte is not None and dte <= r.near_expiry_days:
        return Verdict(
            "CLOSE", "assignment",
            f"{depth:.1f}% in the money with {dte}d left — time has stopped "
            f"being a defence; assignment puts {strike * 100:,.0f} of stock on "
            f"the book", 2)

    if captured >= r.take_profit_pct:
        return Verdict(
            "CLOSE", "target",
            f"{captured:.0f}% of the credit captured ({credit:.2f} sold, "
            f"{mark:.2f} to buy back) — the rest carries the same risk for "
            f"less reward", 2)

    if mark >= credit * r.loss_multiple_of_credit:
        return Verdict(
            "CLOSE", "breach",
            f"costs {mark / credit:.1f}x what was collected "
            f"({credit:.2f} -> {mark:.2f}) — past the "
            f"{r.loss_multiple_of_credit:.0f}x line", 2)

    if itm:
        return Verdict(
            "WATCH", "in the money",
            f"{depth:.1f}% in the money"
            + (f" with {dte}d to recover" if dte is not None else ""), 1)

    return Verdict(
        "HOLD", "", f"out of the money"
        + (f" by {-depth:.1f}%" if depth < 0 else "")
        + (f", {dte}d left" if dte is not None else ""), 0)


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
