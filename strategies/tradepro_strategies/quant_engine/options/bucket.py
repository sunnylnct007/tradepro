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
#:   size    — market cap, because that is what "would I be happy to own
#:             this" actually turns on. This was average daily VOLUME until
#:             21 Oct 2026, which answered a quality question with a
#:             liquidity proxy and failed PG — a name the owner named — at
#:             $0.89bn of turnover against a $370bn market cap. Whether the
#:             option is tradeable is a separate question, already gated on
#:             the thing that decides a fill: its own open interest and
#:             bid-ask spread.

MIN_PRICE_USD = 20.0

# THE SIZE FLOOR, which is what "would I own this" actually turns on.
#
# $30bn, RAISED FROM $10bn on 22 Oct 2026 — and the reason is the owner's, not
# a tuning exercise.
#
# $10bn is the conventional large-cap boundary, and applied to the derived
# universe it qualified 642 names. The owner's reply was immediate and right:
# "this is the thing we shdnbt expand to 8 timres ... too many symbols are
# noise". The board already showed 82 rows to surface 34, and the card that
# ranks them carries its own complaint about exactly that.
#
# $30bn is where the owner's OWN examples sit. Every name they have named is at
# or above it — PG $370bn, KO $402bn, MSFT $3,949bn, NVDA $5,227bn, GOOGL
# $4,162bn — and MDB at $31bn is the smallest they have called good ("best put
# option i have see is mongo db"). So the floor is drawn at the edge of the
# set they described, not fitted to make a count look tidy.
#
# Measured effect: 642 -> 338 names, and MDB survives. $50bn would cut to 256
# and lose MDB, which is the name that started this.
#
# Sanity-checked against the names the owner NAMED, 21 Oct 2026:
#   PG $370bn · KO $402bn · MSFT $3,949bn · NVDA $5,227bn · GOOGL $4,162bn
#   CRWV $34bn — "core weave is fine ... its a good name" — passes comfortably
MIN_MARKET_CAP_USD = 30_000_000_000.0

#: A dividend is not required — GOOGL and NVDA pay little or nothing and are
#: exactly the names named. It is recorded as context, never as a gate.

#: WHETHER PREMIUM IS RICH IS NOT DECIDED HERE — removed 21 Oct 2026.
#:
#: Two IV/HV thresholds used to live at this spot and gate routing: refuse
#: below 1.05x, floor-and-trade above 1.20x. Both are gone, for two reasons.
#:
#: They DUPLICATED the risk engine, which already answers exactly this question
#: — IV-Rank where the window is deep enough, the variance-premium bridge where
#: it is not — and publishes its reason. One fact decided in two places is the
#: failure shape this desk keeps paying for.
#:
#: And the copy here was the gate the owner disproved. On WDC the ratio passes
#: at a 30-day window and fails at 6-month and 1-year (1.09x against 0.78x /
#: 0.80x); the window was never chosen, it was whatever IBKR returned. He had
#: sold a WDC put and was in profit while this gate said avoid.
#:
#: Routing now answers only "would being assigned this be acceptable". Whether
#: the premium is worth having stays with the engine that gates it.


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
    market_cap_usd: float | None,
    reporting_currency: str | None = None,
    deny: frozenset[str] = frozenset(),
    symbol: str = "",
) -> tuple[bool, str]:
    """Would assignment in this name be acceptable — a question about SIZE.

    THIS USED TO ASK ABOUT TURNOVER, AND THE TWO ARE NOT THE SAME QUESTION.
    The gate required $1bn of average daily dollar volume, which is a
    LIQUIDITY measure, and liquidity was being used to answer a QUALITY
    question. Measured 21 Oct 2026, the two diverge on exactly the names that
    matter:

        PG    $0.89bn ADV  -> FAILED       owner: "good divident paying ones
                                            like PG, coca cola etc"
        PG    $370bn  mcap -> passes

    A 188-year-old consumer staple trades less than a hot new listing, and the
    old gate read that as PG being unsuitable to own. It would have routed the
    owner's own named example away from a cash-secured put and into a spread.

    Liquidity still matters — you cannot get filled at a fair price without it
    — but it is already gated elsewhere, on the thing that actually determines
    the fill: the option's own open interest and bid-ask spread. Those are the
    right instruments for that question. This one is about whether owning 100
    shares is an outcome worth accepting.

    THE CURRENCY IS CHECKED, NOT ASSUMED. `market_cap_usd` is sourced from
    yfinance's `marketCap`, which is denominated in the symbol's REPORTING
    currency despite the field's name — the fundamentals module's own comment
    concedes "absolute USD (or symbol's reporting currency)". SK hynix (SKHY)
    returns currency=USD, financialCurrency=KRW and a market cap of
    1,018,882,555,904, which read as dollars would make it one of the largest
    companies on earth. A non-USD figure is therefore REFUSED rather than
    compared, because comparing it is how a mid-cap gets sold naked puts on
    the strength of an exchange-rate artefact.

    EITHER INPUT MISSING IS A NO. Not because the name is bad, but because we
    cannot show that it is good, and the cost of being wrong is owning 100
    shares of something the owner never wanted.
    """
    if symbol.strip().upper() in deny:
        return False, "explicitly excluded by the owner"
    if price is None:
        return False, "price unavailable — cannot verify it is not a penny stock"
    if price < MIN_PRICE_USD:
        return False, (f"${price:,.2f} is below the ${MIN_PRICE_USD:,.0f} floor — "
                       f"penny and sub-$20 names are excluded")
    if market_cap_usd is None:
        return False, "market cap unavailable — cannot verify it is a large cap"
    ccy = (reporting_currency or "USD").strip().upper()
    if ccy != "USD":
        return False, (f"market cap is reported in {ccy}, not USD — refusing to "
                       f"compare it against a dollar floor rather than guess at "
                       f"an exchange rate")
    if market_cap_usd < MIN_MARKET_CAP_USD:
        return False, (f"${market_cap_usd/1e9:,.1f}bn market cap is below "
                       f"${MIN_MARKET_CAP_USD/1e9:,.0f}bn — a small or mid cap, "
                       f"not a name to be assigned")
    return True, (f"${market_cap_usd/1e9:,.0f}bn market cap at ${price:,.2f} — "
                  f"large enough that being assigned is an acceptable outcome")


def route(
    symbol: str,
    *,
    price: float | None = None,
    market_cap_usd: float | None = None,
    reporting_currency: str | None = None,
    deny: frozenset[str] = frozenset(),
) -> Routing:
    """Decide the STRUCTURE for one name, from ownership alone.

    ROUTING NO LONGER JUDGES WHETHER PREMIUM IS RICH, and removing that is the
    point of this function's current shape. It used to gate on the IV/HV ratio
    twice over — refusing below 1.05x and demanding 1.20x before it would
    floor a name — which was wrong in two separate ways.

    First, it DUPLICATED a judgement the risk engine already makes. The engine
    gates premium richness properly, with IV-Rank where the window is deep
    enough and the variance-premium bridge where it is not, and it publishes
    its reason. Deciding the same question here meant two places could disagree
    about one fact, which is the failure shape this desk keeps paying for.

    Second, the copy here was the gate the owner DISPROVED. On WDC the ratio
    passes at a 30-day window and fails at 6-month and 1-year (1.09x against
    0.78x / 0.80x), and the window was never chosen — it was whatever IBKR
    happened to return. The owner had sold a WDC put and was in profit while
    this gate said avoid. A measure that unreliable has no business deciding
    the SHAPE of a trade on top of deciding whether to take it.

    So the structure question is now the one the owner actually posed: would
    being assigned this name be an acceptable outcome?

        ownable      -> CASH_SECURED_PUT   assignment is fine, sell it naked
        not ownable  -> BULL_PUT_SPREAD    sell the premium, floor the loss

    Whether the premium is worth having at all stays with the risk engine,
    which refuses with a stated reason either way. The practical effect is that
    a name we would not want to own is no longer DISCARDED for failing a
    ratio — it is offered with a floor under it, which is the owner's own
    second bucket: "on names we do not want to have byut techincally are
    paying good premium we could sell spread".
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return Routing(None, "none", "no symbol supplied — nothing to route")

    ownable, why_own = is_ownable(price=price, market_cap_usd=market_cap_usd,
                                  reporting_currency=reporting_currency,
                                  deny=deny, symbol=sym)
    if ownable:
        return Routing(
            Structure.CASH_SECURED_PUT, "own",
            f"{why_own}. Assignment is an acceptable outcome, so the put is "
            f"sold naked and cash-secured.")

    return Routing(
        Structure.BULL_PUT_SPREAD, "spread",
        f"not one to own ({why_own}) — so the premium is sold as a SPREAD, with "
        f"the loss floored by the long leg rather than running with the stock. "
        f"SKHY and CRWV were exactly this case and were sold naked: -1,998 "
        f"and -602.")


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
    price: float | None = None,
    market_cap_usd: float | None = None,
    reporting_currency: str | None = None,
    regime=None,
    permitted: frozenset[Structure] | None = None,
    earnings_in_expiry_window: bool | None = None,
    deny: frozenset[str] = frozenset(),
) -> Resolution:
    r = route(symbol, price=price, market_cap_usd=market_cap_usd,
              reporting_currency=reporting_currency, deny=deny)
    if r.structure is None:
        return Resolution(None, r.bucket, r.reason)
    return degrade_for_context(
        r.structure, bucket=r.bucket, reason=r.reason, regime=regime,
        permitted=permitted, earnings_in_expiry_window=earnings_in_expiry_window)


def degrade_for_context(
    structure: Structure,
    *,
    bucket: str = "own",
    reason: str = "",
    regime=None,
    permitted: frozenset[Structure] | None = None,
    earnings_in_expiry_window: bool | None = None,
) -> Resolution:
    """Steps 2 and 3 only — regime and earnings — on a structure the caller has
    ALREADY chosen.

    Split out from `resolve_structure` because the live screen needs exactly
    this half and must not yet have the other one. Step 1 (ownership) depends
    on average daily traded volume, which the screen does not currently carry,
    and on the IV/HV ratio gate the owner has correctly challenged (WDC passes
    at 30d and fails at 6m; the window was never chosen, it was inherited from
    whatever IBKR returned). Routing on either today would invent refusals on
    the very names most worth selling — MSFT and NVDA would read as "not one to
    own" purely because the volume field is absent.

    So the screen degrades but does not route. These two steps can only ever
    turn a REFUSAL into a floored trade, never the reverse, which is why they
    are safe to ship ahead of the third. One implementation, two callers.
    """
    s: Structure | None = structure
    notes: list[str] = []
    r = Routing(structure, bucket, reason)

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
