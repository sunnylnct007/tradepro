"""What to DO about each wheel position — not what it is worth.

The owner runs a cash-secured-put wheel by hand and it works: roughly +$3,346
realised in seven weeks against a £113,756 account. What the platform has never
done is help him manage it. The screen lists candidates to OPEN. Nothing has
ever said "this one needs a decision today", which is how APLD came to sit at
-$997 with a covered call earning $0.35 that cannot repair it.

THESE RULES ARE STANDARD WHEEL MANAGEMENT, NOT AN EDGE I AM CLAIMING.
They are the conventional practice of premium sellers, and they are written
down here so every recommendation can be traced to one and argued with:

  1. TAKE PROFIT AT 50%. A short put that has given up half its premium has
     given up most of its expected value; the remaining half is earned slowly
     and at rising risk. Close and redeploy.

  2. MANAGE AT 21 DTE. Gamma rises sharply into the last three weeks — the
     position starts moving like stock rather than like an option. Roll or
     close rather than hold to expiry, whichever the moneyness argues for.

  3. A TESTED PUT IS A DECISION, NOT A DISASTER. Spot below strike means
     assignment is likely. That is the wheel working as intended IF you still
     want the shares at that price. The rule is to ask, not to panic.

  4. A COVERED CALL THAT CANNOT REPAIR IS NOT A REPAIR. This is the APLD case
     and the one nothing was surfacing. If the stock sits far below your cost
     basis, a call struck at cost basis is deep out of the money, earns pennies,
     and commits you to another cycle of holding. Writing it is not managing
     the position — it is being paid a token to keep waiting.

Every output carries its rule number and its arithmetic, so a decision can be
rejected on its reasoning rather than accepted on its authority.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

# ── the thresholds, in one place, each with the reason it has that value ──
TAKE_PROFIT_PCT = 0.50      # rule 1 — half the premium captured
MANAGE_DTE = 21             # rule 2 — gamma ramp
#: TIME TO REPAIR — how many months of covered-call premium, at the rate you
#: are currently earning it, would be needed to cover the unrealised loss on
#: the shares.
#:
#: My first version of this rule tested the premium in DOLLARS ("under $0.30 is
#: pennies"). That was badly constructed and it failed on the exact case it was
#: written for: APLD's call pays $0.35, so the rule did not fire, and $0.35 on
#: 100 shares is $35 against about $2 of commission — not pennies at all.
#:
#: The premium size was never the point. The question is whether writing calls
#: can plausibly dig the position out, and that is a RATE against a HOLE:
#: 1.2% of spot per 24 days is about 1.9%/month, and a 26% hole then takes
#: fourteen months. Stated that way it is a decision anyone can check, it does
#: not depend on a dollar threshold that changes with the share price, and it
#: generalises to any name.
REPAIR_MONTHS_LIMIT = 12

_OPT = re.compile(
    r"^(?P<sym>[A-Z.]+)\s+(?P<mon>[A-Za-z]{3})(?P<day>\d{1,2})'(?P<yr>\d{2})\s+"
    r"(?P<strike>[\d.]+)\s+(?P<kind>PUT|CALL)", re.I)


@dataclass
class Decision:
    symbol: str
    what: str                  # the position, in words
    action: str                # HOLD | CLOSE | ROLL | DECIDE | STOP-WRITING
    rule: str                  # which rule above, by number
    why: str                   # the arithmetic
    urgency: int = 0           # higher sorts first


@dataclass
class WheelBook:
    decisions: list[Decision] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)


def _parse_option(desc: str):
    m = _OPT.match((desc or "").strip())
    if not m:
        return None
    try:
        expiry = _dt.datetime.strptime(
            f"{m['day']} {m['mon']} 20{m['yr']}", "%d %b %Y").date()
    except ValueError:
        return None
    return m["sym"].upper(), expiry, float(m["strike"]), m["kind"].upper()


def review(positions: list[dict], today: _dt.date | None = None) -> WheelBook:
    """positions: IBKR-shaped rows (contract_description, position,
    market_price, average_price, asset_class)."""
    today = today or _dt.date.today()
    book = WheelBook()

    stock = {}
    for p in positions:
        if (p.get("asset_class") or "").upper() == "STK":
            sym = (p.get("contract_description") or "").split()[0].upper()
            stock[sym] = p

    for p in positions:
        if (p.get("asset_class") or "").upper() != "OPT":
            continue
        parsed = _parse_option(p.get("contract_description") or "")
        if not parsed:
            book.unparsed.append(p.get("contract_description") or "?")
            continue
        sym, expiry, strike, kind = parsed
        qty = float(p.get("position") or 0)
        if qty >= 0:                      # only SHORT premium is the wheel
            continue
        sold_at = float(p.get("average_price") or 0)
        now = float(p.get("market_price") or 0)
        dte = (expiry - today).days
        captured = (sold_at - now) / sold_at if sold_at > 0 else 0.0
        what = f"short {kind.lower()} {strike:g} exp {expiry:%d %b}, sold {sold_at:.2f}, now {now:.2f}"

        # RULE 4 IS CHECKED FIRST, and the ordering is the point.
        #
        # The first version of this put take-profit first, and APLD's call
        # matched it at 68% captured — so the output said "CLOSE, nice profit"
        # and never mentioned that the underlying is 26% below cost and the
        # call cannot lift it. Both are true; only one of them is a decision
        # about the STRATEGY rather than the trade. Closing a call at 68% is
        # tactical and obvious. Whether to keep wheeling a name that has fallen
        # this far is the question that was never being asked, and burying it
        # under a profit notice is how APLD came to sit unexamined.
        if kind == "CALL" and sym in stock:
            st = stock[sym]
            spot = float(st.get("market_price") or 0)
            basis = float(st.get("average_price") or 0)
            gap = (strike - spot) / spot if spot > 0 else 0
            loss_pct = (basis - spot) / basis if basis else 0.0
            # premium earned per month, as a fraction of the share price
            months_of_life = max((expiry - today).days, 1) / 30.44
            # WHAT IT CAN EARN FROM HERE, not what it earned. `sold_at` is
            # history — APLD's call fetched 1.10 when the stock was higher, and
            # using that gave a 5-month repair estimate that flattered the
            # position. The forward rate is what the same strike is worth NOW
            # (0.35), which is 1.6%/month and puts repair at seventeen months.
            # A decision about whether to keep holding has to be priced at
            # today's premium, not at the premium of a better day.
            rate_per_month = (now / spot / months_of_life) if spot > 0 and months_of_life else 0
            repair_months = (loss_pct / rate_per_month) if rate_per_month > 0 else float("inf")
            if loss_pct > 0 and repair_months > REPAIR_MONTHS_LIMIT:
                extra = (f" Close the call now — {captured:.0%} of its premium is captured."
                         if captured >= TAKE_PROFIT_PCT else "")
                book.decisions.append(Decision(
                    sym, what, "STOP-WRITING", "rule 4 · calls cannot dig this out",
                    f"stock {spot:.2f} vs your cost {basis:.2f} — a {loss_pct:.0%} hole. "
                    f"That same strike is worth {now:.2f} today, which is {rate_per_month:.1%} of the "
                    f"share price per month. At that rate covering the hole takes about "
                    f"{repair_months:.0f} MONTHS, and the {strike:g} strike is {gap:.0%} away so "
                    f"it will not be exercised and lift the position off you."
                    f"{extra} THE DECISION IS ABOUT THE STOCK, not the call: keep waiting for a "
                    f"recovery, or take the loss and redeploy the capital.",
                    urgency=4))
                continue

        # rule 1 — take profit
        if captured >= TAKE_PROFIT_PCT:
            book.decisions.append(Decision(
                sym, what, "CLOSE", "rule 1 · take profit at 50%",
                f"{captured:.0%} of the premium is already captured "
                f"({sold_at:.2f} → {now:.2f}). The remaining {sold_at-now:.2f} "
                f"is earned over {dte} more days at rising risk. Closing frees "
                f"the collateral to sell again.", urgency=2))
            continue

        # rule 3 — tested put
        if kind == "PUT" and sym in stock:
            spot = float(stock[sym].get("market_price") or 0)
            if spot and spot < strike:
                book.decisions.append(Decision(
                    sym, what, "DECIDE", "rule 3 · the put is tested",
                    f"spot {spot:.2f} is below the {strike:g} strike with {dte} days left. "
                    f"Assignment is likely. That is the wheel working IF you still want "
                    f"{sym} at {strike:g} — if you do not, roll down and out now.",
                    urgency=2))
                continue

        # rule 2 — manage into expiry
        if dte <= MANAGE_DTE:
            book.decisions.append(Decision(
                sym, what, "ROLL", "rule 2 · manage at 21 DTE",
                f"{dte} days left with {captured:.0%} captured. Gamma rises sharply "
                f"from here — the position starts behaving like stock. Roll out to "
                f"restore time value rather than holding into expiry.", urgency=1))
            continue

        book.decisions.append(Decision(
            sym, what, "HOLD", "—",
            f"{captured:.0%} captured, {dte} days left. Nothing to do: below the 50% "
            f"take-profit and outside the 21-day management window.", urgency=0))

    book.decisions.sort(key=lambda d: (-d.urgency, d.symbol))
    return book
