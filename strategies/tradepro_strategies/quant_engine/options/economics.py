"""What a short-premium trade actually risks and earns — before it is placed.

Owner, 20 Oct 2026: *"options we should be able to determine as well on spread
the max loss and gain"*, and *"i want a trustable screen where i can go in the
morning and decide what option i should be investing in and what i should be
avoiding or closing"*.

A candidate row that shows a credit and a delta but not the worst case is the
row that let SKHY be sold naked: credit $510 against a loss now past $1,998 and
still open. The max loss is the number that would have changed the decision, so
it is computed here and shown on every row rather than left as an exercise.

EVERYTHING IS PER CONTRACT, in dollars, at the real multiplier (100). The
owner's whole option book is 1-lot, and the arithmetic that matters is "what
does this cost me if I am wrong", not a percentage.

NO MODELLED PRICES. Credit comes from a real bid/ask mid — available since the
OPRA subscription went on, 20 Oct 2026. Before that every premium on this desk
was Black-Scholes off a stale spot, which is why the wheel board's yields were
never comparable to anything real.
"""
from __future__ import annotations

from dataclasses import dataclass

#: US equity options. One contract = 100 shares.
MULTIPLIER = 100


@dataclass(frozen=True)
class Economics:
    """The four numbers a decision actually needs, plus the two that size it."""
    max_gain: float          # credit kept if it expires worthless
    max_loss: float          # worst case at expiry (None-free: always bounded or stated)
    breakeven: float         # underlying price at which the trade nets zero
    capital: float           # cash the broker will hold against it
    return_on_risk: float    # max_gain / max_loss — the honest yield
    unbounded: bool          # True = a naked short put; max_loss is to ZERO

    def as_row(self) -> dict:
        return {
            "maxGain": round(self.max_gain, 2),
            "maxLoss": round(self.max_loss, 2),
            "breakeven": round(self.breakeven, 2),
            "capital": round(self.capital, 2),
            "returnOnRisk": round(self.return_on_risk, 4),
            "unbounded": self.unbounded,
        }


def cash_secured_put(*, strike: float, credit_per_share: float) -> Economics:
    """A naked short put, cash-secured.

    MAX LOSS IS TO ZERO, and saying so is the point. A CSP is often described
    as "defined risk because you'd own the stock anyway" — true for a name you
    want, and the reason the bucket router only allows this shape on names that
    pass the ownable test. It is still the whole strike if the name goes to
    nothing, and the row says that rather than quietly reporting the credit.
    """
    credit = credit_per_share * MULTIPLIER
    loss_to_zero = strike * MULTIPLIER - credit
    return Economics(
        max_gain=credit,
        max_loss=loss_to_zero,
        breakeven=strike - credit_per_share,
        capital=strike * MULTIPLIER,          # broker holds the full strike
        return_on_risk=credit / loss_to_zero if loss_to_zero > 0 else 0.0,
        unbounded=True,
    )


def bull_put_spread(
    *, short_strike: float, long_strike: float,
    short_credit_per_share: float, long_debit_per_share: float,
) -> Economics:
    """Sell a put, buy a lower one. The long leg IS the floor.

    SKHY, 28 Sep: sold the 165 put for 5.10, a credit of $510. Naked, the loss
    is now past $1,998 and the trade is still open. As a 165/155 spread the
    worst case is the $1,000 width less the net credit — roughly $490, fixed on
    the day it was opened and incapable of growing.
    """
    if long_strike >= short_strike:
        raise ValueError(
            f"a bull put spread sells the HIGHER strike: short {short_strike} "
            f"must be above long {long_strike}")
    net = (short_credit_per_share - long_debit_per_share) * MULTIPLIER
    width = (short_strike - long_strike) * MULTIPLIER
    loss = width - net
    return Economics(
        max_gain=net,
        max_loss=max(loss, 0.0),
        breakeven=short_strike - (net / MULTIPLIER),
        capital=width,                         # the broker holds the width
        return_on_risk=net / loss if loss > 0 else 0.0,
        unbounded=False,
    )


def compare_naked_vs_spread(
    *, strike: float, credit_per_share: float,
    width: float, long_debit_per_share: float,
) -> dict:
    """What the floor costs, side by side.

    The spread always earns LESS. That is the trade, and showing both is how a
    decision gets made rather than argued — this is the arithmetic that was
    missing when SKHY and CRWV were sold naked.
    """
    naked = cash_secured_put(strike=strike, credit_per_share=credit_per_share)
    spread = bull_put_spread(
        short_strike=strike, long_strike=strike - width,
        short_credit_per_share=credit_per_share,
        long_debit_per_share=long_debit_per_share)
    return {
        "naked": naked.as_row(),
        "spread": spread.as_row(),
        "giveUp": round(naked.max_gain - spread.max_gain, 2),
        "lossAvoided": round(naked.max_loss - spread.max_loss, 2),
        "capitalFreed": round(naked.capital - spread.capital, 2),
    }
