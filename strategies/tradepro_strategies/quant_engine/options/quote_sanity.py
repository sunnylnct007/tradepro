"""Option-quote invariants — the platform catching its own bad numbers.

Owner, 14 Aug 2026: *"I need a trading decision platform where I can reliably
trust the numbers."* Options are arithmetic, so most bad data is **provably**
bad: a quote can violate a no-arbitrage identity, and when it does the right
response is to refuse the row as a DATA DEFECT rather than treat it as a
market signal.

Two failures this week motivated it:

1. A single MU pull returned a stock quote ~2.4 hours newer than the option
   marks on the same underlying, which produced a 960 put priced ABOVE a 960
   call at spot 962 — impossible under put-call parity, and invisible to
   every gate we had.
2. A screen gate shipped with a value that did not mean what it claimed
   (implied vol ranked inside a realised-vol distribution). Nothing in the
   system objected, because nothing was checking.

Every function here is PURE and returns violations with the arithmetic
spelled out, so a reader can verify the verdict by hand. A violation is
never a market opinion — it means *this quote cannot be true*, and the
caller must block rather than rank.

Deliberately NOT included: anything requiring a view on fair value. These
are identities and bounds only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Violation:
    check: str
    detail: str          # the arithmetic, with real numbers
    severity: str = "block"   # "block" (impossible) | "warn" (suspicious)

    def to_dict(self) -> dict:
        return {"check": self.check, "detail": self.detail, "severity": self.severity}


@dataclass
class SanityReport:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(v.severity == "block" for v in self.violations)

    def to_dict(self) -> dict:
        return {"ok": self.ok,
                "violations": [v.to_dict() for v in self.violations]}


def check_bid_ask(bid: float | None, ask: float | None) -> list[Violation]:
    """bid ≤ ask, and neither negative. A crossed book is a stale/mixed feed,
    not an opportunity."""
    out: list[Violation] = []
    if bid is not None and bid < 0:
        out.append(Violation("bid_non_negative", f"bid {bid} < 0"))
    if ask is not None and ask < 0:
        out.append(Violation("ask_non_negative", f"ask {ask} < 0"))
    if bid is not None and ask is not None and bid > ask:
        out.append(Violation(
            "book_not_crossed",
            f"bid {bid:.2f} > ask {ask:.2f} — crossed book, quotes are from "
            f"different moments or one side is stale"))
    return out


def check_intrinsic_floor(kind: str, strike: float | None, spot: float | None,
                          mid: float | None, *, tol: float = 0.02) -> list[Violation]:
    """An option cannot be worth less than exercising it right now.
    put intrinsic = max(K − S, 0); call intrinsic = max(S − K, 0).
    `tol` allows a small quoting/rounding slack before calling it impossible."""
    if None in (strike, spot, mid) or mid is None:
        return []
    k = str(kind).lower()
    intrinsic = max(strike - spot, 0.0) if k.startswith("p") else max(spot - strike, 0.0)
    if mid + tol < intrinsic:
        return [Violation(
            "intrinsic_floor",
            f"{k} mid {mid:.2f} < intrinsic max({'K−S' if k.startswith('p') else 'S−K'}, 0) "
            f"= max({strike:g} − {spot:.2f}, 0) = {intrinsic:.2f} — below exercise value")]
    return []


def check_iv_band(iv: float | None, *, lo: float = 0.01, hi: float = 5.0) -> list[Violation]:
    """IV is a fraction in a sane band. 0 breaks every vol calc downstream;
    >500% annualised is a parse error, not a market."""
    if iv is None:
        return []
    if iv <= 0:
        return [Violation("iv_positive", f"IV {iv} ≤ 0 — a zero/negative vol poisons every "
                                         f"vol-derived figure")]
    if not (lo <= iv <= hi):
        return [Violation("iv_band", f"IV {iv:.1%} outside sane band "
                                     f"[{lo:.0%}, {hi:.0%}] — likely a units/parse error")]
    return []


def check_put_call_parity(call_mid: float | None, put_mid: float | None,
                          spot: float | None, strike: float | None,
                          *, dte: int | None = None, rate: float = 0.04,
                          div_yield: float = 0.0,
                          slack: float | None = None) -> list[Violation]:
    """American put-call parity BOUNDS — the check that catches the MU case
    (a 960 put marked above the 960 call at spot 962).

    For American options parity is an inequality, not an equality:

        S·e^(−qT) − K  ≤  C − P  ≤  S·e^(−qT) − K·e^(−rT)

    The band's own width (K − K·e^(−rT)) is the honest tolerance — no
    invented percentage. A small `slack` absorbs bid-ask noise; it defaults
    to max($0.10, 0.2% of spot). Landing OUTSIDE the band is not an edge:
    it means the two legs, or a leg and the spot, were sampled at different
    moments."""
    if None in (call_mid, put_mid, spot, strike) or dte is None:
        return []
    t = max(dte, 0) / 365.0
    fwd_spot = spot * math.exp(-div_yield * t)
    lower = fwd_spot - strike
    upper = fwd_spot - strike * math.exp(-rate * t)
    tol = slack if slack is not None else max(0.10, 0.002 * abs(spot))
    lhs = call_mid - put_mid
    if lower - tol <= lhs <= upper + tol:
        return []
    where = "below" if lhs < lower else "above"
    return [Violation(
        "put_call_parity",
        f"C − P = {call_mid:.2f} − {put_mid:.2f} = {lhs:.2f}, {where} the "
        f"American no-arbitrage band [S·e^(−qT) − K, S·e^(−qT) − K·e^(−rT)] = "
        f"[{lower:.2f}, {upper:.2f}] (slack ±{tol:.2f}) — the legs were priced "
        f"at different moments; this quote cannot be true")]


def check_freshness(field_ages_sec: dict[str, float], *,
                    max_skew_sec: float = 900.0,
                    max_age_sec: float = 3600.0) -> list[Violation]:
    """Mixed-freshness detection (the standing constraint: every field carries
    its own as_of; a computation carries the OLDEST). Two failures:
    excessive SKEW between fields used in one calculation (the MU case: a
    spot 2.4h newer than the marks), and everything simply being too old."""
    out: list[Violation] = []
    ages = {k: v for k, v in (field_ages_sec or {}).items() if v is not None}
    if not ages:
        return out
    oldest_k, oldest = max(ages.items(), key=lambda kv: kv[1])
    newest_k, newest = min(ages.items(), key=lambda kv: kv[1])
    skew = oldest - newest
    if skew > max_skew_sec:
        out.append(Violation(
            "freshness_skew",
            f"'{oldest_k}' is {oldest / 60:.0f}min old but '{newest_k}' is "
            f"{newest / 60:.0f}min old — {skew / 60:.0f}min skew across fields "
            f"combined in one number (cap {max_skew_sec / 60:.0f}min)"))
    if oldest > max_age_sec:
        out.append(Violation(
            "staleness",
            f"oldest input '{oldest_k}' is {oldest / 60:.0f}min old "
            f"(cap {max_age_sec / 60:.0f}min)", severity="warn"))
    return out


def sanity_report(*, kind: str = "put", strike: float | None = None,
                  spot: float | None = None, bid: float | None = None,
                  ask: float | None = None, mid: float | None = None,
                  iv: float | None = None, dte: int | None = None,
                  paired_mid: float | None = None,
                  rate: float = 0.04, div_yield: float = 0.0,
                  field_ages_sec: dict[str, float] | None = None) -> SanityReport:
    """Run every applicable invariant. `paired_mid` is the SAME strike/expiry
    on the other side (the call, when checking a put) — supply it and parity
    is checked too; omit it and parity is simply skipped, never assumed."""
    v: list[Violation] = []
    v += check_bid_ask(bid, ask)
    v += check_intrinsic_floor(kind, strike, spot, mid)
    v += check_iv_band(iv)
    if paired_mid is not None:
        call_mid = paired_mid if str(kind).lower().startswith("p") else mid
        put_mid = mid if str(kind).lower().startswith("p") else paired_mid
        v += check_put_call_parity(call_mid, put_mid, spot, strike,
                                   dte=dte, rate=rate, div_yield=div_yield)
    v += check_freshness(field_ages_sec or {})
    return SanityReport(violations=v)


__all__ = ["Violation", "SanityReport", "sanity_report", "check_bid_ask",
           "check_intrinsic_floor", "check_iv_band", "check_put_call_parity",
           "check_freshness"]
