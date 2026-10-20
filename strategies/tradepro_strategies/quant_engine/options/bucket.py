"""WHICH STRUCTURE for this name — cash-secured put, spread, or nothing.

Owner, 20 Oct 2026: *"we can have cash secured put on names we want to have …
on names we do not want to have but technically are paying good premium we
could sell spread … and then there will be some names with high win rate
already selling OTM"*.

The risk engine already knows HOW to grade a CASH_SECURED_PUT and a
BULL_PUT_SPREAD, and which regimes permit them. What was missing is the rule
that decides WHICH ONE a given name should be traded as. That decision is this
module and nothing else.

WHY IT MATTERS, measured on the owner's own 52 option legs (3 Aug – 20 Oct):

    closed only            97% win   +$7,576
    open legs              38% win   -$3,852
    EVERYTHING             80% win   +$3,724      <- the honest number

The losses are concentrated in names the owner never wanted to own and sold
NAKED anyway:

    SKHY  sold 5.10, marked 25.07   -1,998 and still running
    CRWV  sold 5.55, marked 11.56     -602

Both fail the ownable test below on measurement — SKHY and CRWV are not names
to hold 100 shares of — so both route to a SPREAD. As 10-wide spreads their
worst case is roughly -490 and -445 instead of unbounded. THAT is the point of
routing by bucket: the same view, with a floor under it.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not decide whether the trade
is any good — that is `risk.evaluate`, which still has to pass. Routing only
answers "if we trade this name, in what shape". A name can route to
BULL_PUT_SPREAD and still be blocked for every other reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from .risk import Structure

#: WHAT "A NAME I WOULD OWN" MEANS, MEASURED.
#:
#: Owner, 20 Oct 2026: *"names i would get assigned are the ones that are good
#: ones like google, msft, nvda etc or good dividend paying ones like PG,
#: coca cola etc … i do not want to do any penny stock trading … or small cap
#: ones"*.
#:
#: My first version of this was a hand-written list derived from past trades.
#: It contained APLD — a $19.50 stock on $388m of daily volume paying no
#: dividend — which is precisely what the owner just excluded. A list cannot
#: express "good large caps and solid dividend payers"; two measurements can,
#: and they cannot drift the way a list does. This desk's recurring infra
#: failure is the hand-maintained allow-list, and an opinion about which names
#: are "good" is the easiest kind to let rot.
#:
#: Thresholds, with the reasoning rather than round numbers for their own sake:
#:   price   — a penny stock is excluded by price alone, and a sub-$20 name
#:             makes cash-secured sizing lumpy (one contract = 100 shares).
#:   volume  — $1bn of average daily traded value is a large-cap signal that
#:             needs no market-cap feed. Measured 20 Oct: WDC $2.88bn, APLD
#:             $388m. It also guarantees the option chain is worth quoting.
MIN_PRICE_USD = 20.0
MIN_AVG_DAILY_USD_VOLUME = 1_000_000_000.0

#: A dividend is not required — GOOGL and NVDA pay little or nothing and are
#: exactly the names named. It is recorded as context, never as a gate.

#: A short put is only worth selling when implied vol exceeds what the stock
#: actually does — that gap IS the edge. Measured on WDC, 20 Oct: implied 67.0%
#: against realised 72.8%, i.e. you are paid LESS than the stock moves. Selling
#: there is negative expectancy before a single other gate.
#:
#: 1.05 rather than 1.00 so a coin-flip margin does not qualify.
MIN_IV_TO_HV = 1.05

#: Above this the premium is rich enough that a name the owner does NOT want to
#: own is still worth trading — with a floor under it. Below it, pass.
RICH_IV_TO_HV = 1.20


@dataclass(frozen=True)
class Routing:
    """What shape to trade, and WHY — the reason travels with the decision so a
    refusal is never a bare 'no'."""
    structure: Structure | None          # None = do not trade this name
    bucket: str                          # own | spread | income | none
    reason: str


def is_ownable(
    *,
    price: float | None,
    avg_daily_usd_volume: float | None,
    deny: frozenset[str] = frozenset(),
    symbol: str = "",
) -> tuple[bool, str]:
    """Would assignment in this name be acceptable — measured, not listed.

    EITHER INPUT MISSING IS A NO. Not because the name is bad, but because we
    cannot show that it is good, and the cost of being wrong here is owning 100
    shares of something the owner never wanted.
    """
    if symbol.strip().upper() in deny:
        return False, "explicitly excluded by the owner"
    if price is None or avg_daily_usd_volume is None:
        return False, "price or traded volume unavailable — cannot verify it is a large cap"
    if price < MIN_PRICE_USD:
        return False, (f"${price:,.2f} is below the ${MIN_PRICE_USD:,.0f} floor — "
                       f"penny and sub-$20 names are excluded")
    if avg_daily_usd_volume < MIN_AVG_DAILY_USD_VOLUME:
        return False, (f"${avg_daily_usd_volume/1e6:,.0f}m average daily volume is below "
                       f"${MIN_AVG_DAILY_USD_VOLUME/1e9:,.0f}bn — not a large cap")
    return True, (f"${price:,.2f} on ${avg_daily_usd_volume/1e9:,.1f}bn average daily "
                  f"volume — large and liquid enough to own")


def route(
    symbol: str,
    *,
    implied_vol: float | None,
    historical_vol: float | None,
    price: float | None = None,
    avg_daily_usd_volume: float | None = None,
    deny: frozenset[str] = frozenset(),
) -> Routing:
    """Decide the structure for one name.

    implied_vol / historical_vol are annualised fractions (0.67 = 67%), as the
    IBKR snapshot returns them. EITHER BEING None IS A REFUSAL, not a default:
    without both we cannot tell whether the premium is worth having, and this
    desk's rule is that an unverifiable gate blocks rather than passes.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return Routing(None, "none", "no symbol supplied — nothing to route")

    if implied_vol is None or historical_vol is None:
        return Routing(None, "none",
                       "implied or realised vol unavailable — cannot tell whether "
                       "the premium is worth selling, so not traded")
    if historical_vol <= 0:
        return Routing(None, "none", "realised volatility is zero or negative — unusable, so the premium cannot be judged rich or cheap")

    ratio = implied_vol / historical_vol

    # THE ECONOMIC GATE, BEFORE ANY PREFERENCE. Selling premium when implied
    # sits below realised is paying someone to take your risk, regardless of
    # how much you like the name.
    if ratio < MIN_IV_TO_HV:
        return Routing(
            None, "none",
            f"implied {implied_vol:.0%} vs realised {historical_vol:.0%} "
            f"({ratio:.2f}x) — you would be paid less than the stock moves")

    ownable, why_own = is_ownable(price=price, avg_daily_usd_volume=avg_daily_usd_volume,
                                  deny=deny, symbol=sym)
    if ownable:
        return Routing(
            Structure.CASH_SECURED_PUT, "own",
            f"{why_own}; implied {implied_vol:.0%} vs realised {historical_vol:.0%} "
            f"({ratio:.2f}x). Assignment is an acceptable outcome, so the put is "
            f"sold naked and cash-secured.")

    # NOT a name to own. Premium alone decides between a floored trade and none.
    if ratio >= RICH_IV_TO_HV:
        return Routing(
            Structure.BULL_PUT_SPREAD, "spread",
            f"not one to own ({why_own}), but implied {implied_vol:.0%} vs realised "
            f"{historical_vol:.0%} ({ratio:.2f}x) pays well — sold as a SPREAD so "
            f"the loss has a floor. SKHY and CRWV were exactly this case and were "
            f"sold naked: -1,998 and -602.")

    return Routing(
        None, "none",
        f"not one to own ({why_own}) and the premium is only {ratio:.2f}x realised "
        f"— not rich enough to carry a name you do not want")


def summarise(routings: dict[str, Routing]) -> dict[str, int]:
    """Counts per bucket, for the screen header. A screen that shows only its
    passes hides how much it refused, and this desk grades refusals."""
    out: dict[str, int] = {}
    for r in routings.values():
        out[r.bucket] = out.get(r.bucket, 0) + 1
    return out


#: Structures that express the SAME view as the key, in order of preference.
#: A fallback may only swap HOW a view is expressed, never WHICH view.
#:
#: A cash-secured put and a bull put spread are both "I am happy to be long
#: here"; the spread just has a floor. A bear put spread is the opposite view
#: and is NOT a fallback for either, however permitted it may be.
SAME_VIEW_FALLBACKS: dict[Structure, tuple[Structure, ...]] = {
    Structure.CASH_SECURED_PUT: (Structure.BULL_PUT_SPREAD,),
}


def permitted_or_fallback(
    structure: Structure | None,
    regime,
    permitted: frozenset[Structure],
) -> tuple[Structure | None, str]:
    """The structure to actually evaluate in this regime.

    MEASURED ON THE LIVE BOARD, 20 Oct 2026. Of 82 screened names, 2 were
    eligible. The single largest refusal after earnings was:

        40x  CASH_SECURED_PUT not permitted in ORANGE regime
             (allowed: BULL_PUT_SPREAD, PROTECTIVE_PUT, COLLAR)

    Forty names where the engine ALREADY NAMED the permitted structure in its
    own refusal, and the screen threw the candidate away instead of offering
    it. That is the owner's bucket-2 case — "on names we do not want to have
    but technically are paying good premium we could sell spread" — arriving
    as a block message rather than a trade.

    ONLY SAME-VIEW SWAPS. RED permits BEAR_PUT_SPREAD and COLLAR; neither
    expresses "happy to be long here", so a RED name still refuses. A fallback
    that flips the view would be inventing a trade nobody asked for.
    """
    if structure is None:
        return None, ""
    if structure in permitted:
        return structure, ""
    for alt in SAME_VIEW_FALLBACKS.get(structure, ()):
        if alt in permitted:
            return alt, (
                f"{structure.value} is not permitted in {getattr(regime, 'value', regime)}; "
                f"traded as {alt.value} instead — same view, with a floor under it")
    return None, (
        f"{structure.value} is not permitted in {getattr(regime, 'value', regime)} and no "
        f"permitted structure expresses the same view "
        f"(permitted: {', '.join(sorted(x.value for x in permitted)) or 'none'})")


# ── One structure decision, one place ──────────────────────────────────────
#
# WHY THIS FUNCTION EXISTS. Both screen call sites built their candidate as a
# hardcoded `Structure.CASH_SECURED_PUT`, which is the shape that keeps costing
# this desk days: one fact in two places, and a fix lands at one of them. Every
# reason a name can be steered to a different structure now composes HERE, and
# the call sites ask rather than decide.
#
# There are three such reasons, and they stack in this order:
#   1. OWNERSHIP  — would assignment in this name be acceptable? A name we do
#                   not want to own can still be worth selling premium on, with
#                   a floor under it. (route())
#   2. REGIME     — ORANGE forbids an unbounded short put but permits a spread.
#                   Refused 40 of 82 names on the live board. (#309)
#   3. EARNINGS   — a print inside the expiry window forbids an unbounded bet
#                   on a known date, but not a bounded one. Refused 50 of 82,
#                   the largest single refusal. (#310)
#
# All three express the SAME VIEW with a floor under it, never a different
# view: a CSP degrades to a BULL_PUT_SPREAD and never to a BEAR_PUT_SPREAD.
# Reasons accumulate instead of overwriting, because "we traded this as a
# spread" and WHY are the two halves of a trustworthy row.

UNBOUNDED_SHORT_PREMIUM: frozenset[Structure] = frozenset({
    Structure.CASH_SECURED_PUT, Structure.COVERED_CALL, Structure.WHEEL,
})


@dataclass(frozen=True)
class Resolution:
    """The structure to trade, the bucket it came from, and every reason it is
    not the structure we first wanted. `notes` is empty on the plain path."""
    structure: Structure | None
    bucket: str
    reason: str
    notes: tuple[str, ...] = ()

    @property
    def downgraded(self) -> bool:
        return bool(self.notes)


def resolve_structure(
    symbol: str,
    *,
    implied_vol: float | None,
    historical_vol: float | None,
    price: float | None = None,
    avg_daily_usd_volume: float | None = None,
    regime=None,
    permitted: frozenset[Structure] | None = None,
    earnings_in_expiry_window: bool | None = None,
    deny: frozenset[str] = frozenset(),
) -> Resolution:
    r = route(symbol, implied_vol=implied_vol, historical_vol=historical_vol,
              price=price, avg_daily_usd_volume=avg_daily_usd_volume, deny=deny)
    if r.structure is None:
        return Resolution(None, r.bucket, r.reason)

    s: Structure | None = r.structure
    notes: list[str] = []

    # 2. REGIME.
    if permitted is not None:
        s, note = permitted_or_fallback(s, regime, permitted)
        if note:
            notes.append(note)
        if s is None:
            return Resolution(None, r.bucket, r.reason, tuple(notes))

    # 3. EARNINGS. Only a TRUE print steers the structure. `None` means the
    #    calendar is unavailable, and an absence of data is not an absence of
    #    an event — that case falls through untouched so the risk engine blocks
    #    it, which is the behaviour we want and have pinned.
    if earnings_in_expiry_window is True:
        if s in UNBOUNDED_SHORT_PREMIUM:
            alt = next((a for a in SAME_VIEW_FALLBACKS.get(s, ())
                        if permitted is None or a in permitted), None)
            if alt is None:
                notes.append(
                    f"Earnings fall inside the expiry window and no defined-risk "
                    f"version of {s.value} is available here — an unbounded short "
                    f"over a known event date is not a trade we take.")
                return Resolution(None, r.bucket, r.reason, tuple(notes))
            notes.append(
                f"Earnings fall inside the expiry window, so {s.value} is traded "
                f"as {alt.value} — same view, with the loss capped before the "
                f"print.")
            s = alt
        else:
            # ALREADY defined-risk — nothing to degrade, but the print is still
            # a fact about this trade and the row has to carry it. Reasons
            # ACCUMULATE; the regime arriving first must not swallow the
            # earnings one. (Caught by test_both_reasons_stack: with ORANGE and
            # a print, the structure was right and the row silently reported
            # only one of the two causes.)
            notes.append(
                f"Earnings also fall inside the expiry window. {s.value} caps "
                f"the loss before the print, but the cap is far more likely to "
                f"be reached — size as if it will be.")

    return Resolution(s, r.bucket, r.reason, tuple(notes))
