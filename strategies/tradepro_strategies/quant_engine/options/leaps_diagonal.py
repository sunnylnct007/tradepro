"""LEAPS diagonal / poor-man's covered call (PMCC) — the arithmetic.

Buy a long-dated deep-ITM call (the LEAPS, standing in for 100 shares), sell a
short-dated OTM call against it. Same income idea as a covered call at a
fraction of the capital — and with a failure mode covered calls do not have.

THE ONE CHECK THAT MATTERS
--------------------------
    width = short_strike - long_strike
    if width <= net_debit:  the trade CANNOT make money.

If the spread between the strikes is narrower than what you paid, then even the
best case — stock rises, short call assigned, position closed at maximum value
— returns less than the debit. You lose on a correct directional call. It is
the classic PMCC error, it is invisible unless you compute it, and it is why
this module exists rather than eyeballing a chain.

Everything here is pure and unit-tested. Prices are per-share; contract-level
figures multiply by 100.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not model early assignment on the short call, which is a real risk when
the short goes deep ITM near an ex-dividend date: you are assigned, and must
either exercise the LEAPS (forfeiting its remaining extrinsic) or buy shares.
`early_assignment_warning` flags the exposure; it does not price it. Saying so
here because an unstated omission in an options calculator is how people get
hurt.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiagonalLeg:
    """One leg. `price` is what you actually transact at: the ASK for the long
    leg you buy, the BID for the short leg you sell — never the mid, because a
    mid you cannot fill is a number that flatters the whole structure."""
    strike: float
    price: float
    dte: int
    delta: float | None = None
    iv: float | None = None
    open_interest: int | None = None


@dataclass
class DiagonalEval:
    ok: bool
    net_debit: float                 # per share; capital at risk
    capital_usd: float               # net_debit x 100
    width: float                     # short_strike - long_strike
    max_profit_usd: float | None     # if called away at the short strike
    max_loss_usd: float              # the debit — this is a DEFINED-RISK trade
    breakeven: float | None          # underlying at short expiry, approximate
    return_if_called_pct: float | None
    static_return_pct: float | None  # short premium / debit, one cycle
    annualised_static_pct: float | None
    long_extrinsic: float | None
    short_extrinsic: float | None
    extrinsic_ratio: float | None    # short extrinsic ÷ long extrinsic per day
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    calcs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def _extrinsic(option_price: float, spot: float, strike: float, kind: str) -> float:
    """Time value = price - intrinsic. Never negative: a quote below intrinsic
    is an arbitrage or a bad quote, and either way 0 is the honest floor here
    (the quote-sanity checks catch the underlying defect separately)."""
    intrinsic = max(0.0, (spot - strike) if kind == "call" else (strike - spot))
    return max(0.0, option_price - intrinsic)


def evaluate_diagonal(
    *, spot: float, long_leg: DiagonalLeg, short_leg: DiagonalLeg,
    min_long_delta: float = 0.70,
    min_width_over_debit: float = 1.0,
    max_long_extrinsic_pct: float = 0.15,
    min_long_dte: int = 180,
    max_short_dte: int = 60,
) -> DiagonalEval:
    """Grade a LEAPS diagonal. Returns the arithmetic plus explicit blocks.

    Defaults, and why:
      min_long_delta 0.70      — below this the LEAPS stops tracking the stock
                                 and the structure is a punt, not a stock proxy.
      min_width_over_debit 1.0 — width must EXCEED the debit (see module docs).
                                 1.0 means break-even-at-best; anything at or
                                 under it is blocked outright.
      max_long_extrinsic_pct   — extrinsic on the long leg as a share of its
                                 price. You hold this leg for months and pay
                                 that time value; deep ITM keeps it small.
      min_long_dte 180         — a "LEAPS" with 3 months left is just a call.
      max_short_dte 60         — income comes from selling the steepest part of
                                 the theta curve, not from a second LEAPS.
    """
    blocks: list[str] = []
    warnings: list[str] = []

    net_debit = long_leg.price - short_leg.price
    capital = net_debit * 100.0
    width = short_leg.strike - long_leg.strike

    long_ext = _extrinsic(long_leg.price, spot, long_leg.strike, "call")
    short_ext = _extrinsic(short_leg.price, spot, short_leg.strike, "call")

    # ── THE structural gate ──────────────────────────────────────────────
    if net_debit <= 0:
        blocks.append(
            f"Net debit {net_debit:.2f} is not positive — the short leg pays for "
            f"the long one, which means the quotes are wrong or the strikes are "
            f"inverted. Refusing to grade a structure that cannot exist.")
    elif width <= net_debit * min_width_over_debit:
        blocks.append(
            f"STRUCTURALLY UNPROFITABLE: strike width {width:.2f} ≤ net debit "
            f"{net_debit:.2f}. Even the BEST case — stock rises through the short "
            f"strike and the position is closed at maximum value — returns less "
            f"than you paid. This trade loses money on a CORRECT directional call.")

    max_profit = (width - net_debit) * 100.0 if net_debit > 0 else None
    max_loss = capital
    breakeven = long_leg.strike + net_debit if net_debit > 0 else None
    ret_if_called = ((max_profit / capital * 100.0)
                     if (max_profit is not None and capital > 0) else None)
    static = (short_leg.price / net_debit * 100.0) if net_debit > 0 else None
    annualised = (static * 365.0 / max(short_leg.dte, 1)) if static is not None else None

    # Per-day extrinsic decay: the whole thesis is that the short leg bleeds
    # time value faster than the long one. If it doesn't, you are financing
    # someone else's theta.
    ext_ratio = None
    if long_ext > 0 and short_leg.dte > 0 and long_leg.dte > 0:
        ext_ratio = (short_ext / max(short_leg.dte, 1)) / (long_ext / max(long_leg.dte, 1))
        if ext_ratio < 1.0:
            warnings.append(
                f"Short leg decays SLOWER than the long leg per day (ratio "
                f"{ext_ratio:.2f}) — time is working against this structure, not for it.")

    # ── Leg-quality gates ────────────────────────────────────────────────
    if long_leg.delta is not None and long_leg.delta < min_long_delta:
        blocks.append(
            f"LEAPS delta {long_leg.delta:.2f} < {min_long_delta:.2f} — not deep enough "
            f"ITM to act as a stock substitute; the long leg won't track the move "
            f"you are trying to capture.")
    if long_leg.dte < min_long_dte:
        blocks.append(f"Long leg {long_leg.dte} DTE < {min_long_dte} — that is not a LEAPS.")
    if short_leg.dte > max_short_dte:
        warnings.append(
            f"Short leg {short_leg.dte} DTE > {max_short_dte} — selling too far out gives up "
            f"the steep part of the theta curve.")
    if short_leg.strike <= spot:
        warnings.append(
            f"Short strike {short_leg.strike:.2f} is at or below spot {spot:.2f} — already "
            f"ITM, so assignment is the expected outcome, not the tail.")
    if long_leg.price > 0:
        ext_pct = long_ext / long_leg.price
        if ext_pct > max_long_extrinsic_pct:
            warnings.append(
                f"LEAPS extrinsic is {ext_pct:.0%} of its price ({long_ext:.2f} of "
                f"{long_leg.price:.2f}) — you are paying a lot of time value on a leg "
                f"you intend to hold for months.")

    # Early assignment: a defined-risk structure, but not a costless one.
    if short_leg.strike < spot:
        warnings.append(
            "Short call is ITM — early assignment is possible, especially around an "
            "ex-dividend date. If assigned you must exercise the LEAPS (forfeiting its "
            "remaining extrinsic) or buy shares. Not modelled here.")

    return DiagonalEval(
        ok=not blocks,
        net_debit=round(net_debit, 4),
        capital_usd=round(capital, 2),
        width=round(width, 4),
        max_profit_usd=round(max_profit, 2) if max_profit is not None else None,
        max_loss_usd=round(max_loss, 2),
        breakeven=round(breakeven, 4) if breakeven is not None else None,
        return_if_called_pct=round(ret_if_called, 2) if ret_if_called is not None else None,
        static_return_pct=round(static, 2) if static is not None else None,
        annualised_static_pct=round(annualised, 1) if annualised is not None else None,
        long_extrinsic=round(long_ext, 4),
        short_extrinsic=round(short_ext, 4),
        extrinsic_ratio=round(ext_ratio, 3) if ext_ratio is not None else None,
        blocks=blocks, warnings=warnings,
        calcs={
            "net_debit": f"long ask {long_leg.price:.2f} - short bid {short_leg.price:.2f} "
                         f"= {net_debit:.2f}/share = ${capital:,.0f} at risk",
            "width": f"short {short_leg.strike:.2f} - long {long_leg.strike:.2f} = {width:.2f}",
            "max_profit": (f"(width {width:.2f} - debit {net_debit:.2f}) x 100 = "
                           f"${max_profit:,.0f}" if max_profit is not None else "n/a"),
            "breakeven": (f"long strike {long_leg.strike:.2f} + debit {net_debit:.2f} = "
                          f"{breakeven:.2f} (approximate — ignores LEAPS extrinsic "
                          f"remaining at short expiry, which makes the real breakeven "
                          f"slightly BETTER than this)"
                          if breakeven is not None else "n/a"),
            "return_if_called": (f"${max_profit:,.0f} / ${capital:,.0f} = "
                                 f"{ret_if_called:.1f}%" if ret_if_called is not None else "n/a"),
        },
    )


def select_diagonal_legs(calls: list, spot: float, *,
                         target_long_delta: float = 0.80,
                         target_short_delta: float = 0.25,
                         pricer=None, long_dte: int = 365, short_dte: int = 35):
    """Pick the deep-ITM long and OTM short from a chain by delta.

    Kept separate from `evaluate_diagonal` so the selection rule and the
    arithmetic can be tested — and changed — independently.
    """
    from .chains import delta_of

    if not calls:
        return None, None
    t_long = max(long_dte, 1) / 365.0
    t_short = max(short_dte, 1) / 365.0

    def _d(q, t):
        try:
            return abs(delta_of(q, spot, t, pricer)) if pricer else None
        except Exception:  # noqa: BLE001 — an undeltable quote is simply not selectable
            return None

    longs = [(abs((_d(q, t_long) or 0) - target_long_delta), q) for q in calls
             if q.strike < spot and (_d(q, t_long) or 0) > 0]
    shorts = [(abs((_d(q, t_short) or 0) - target_short_delta), q) for q in calls
              if q.strike > spot and (_d(q, t_short) or 0) > 0]
    long_q = min(longs, key=lambda x: x[0])[1] if longs else None
    short_q = min(shorts, key=lambda x: x[0])[1] if shorts else None
    return long_q, short_q


__all__ = ["DiagonalLeg", "DiagonalEval", "evaluate_diagonal", "select_diagonal_legs"]
