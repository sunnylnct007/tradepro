"""Live book exposure in dollar-delta, by segment (RISK_GUARDRAILS_V1 §2 G6,
options review item 1, 28 Nov 2026).

WHY. The live lane reported capital by sector from the SHORT PUTS' assignment
obligation. Shares, long puts (hedges) and short calls were invisible in that
number, so a book that was net long close to its whole account read as a list
of put rows. The owner worked the real figure out by hand with a script on
27 Nov. This module is that arithmetic, kept pure so it is testable without a
broker:

    delta_dollars = quantity × delta × multiplier × underlying price
    shares: delta 1, multiplier 1; options: multiplier 100, delta from the
    live chain (or, when the chain has none, Black-Scholes from the option's
    mark — labelled), sign from the position (short put = positive exposure,
    long put = negative).

Grouped by the owner's SEGMENT MAP (settings-kv `risk_guardrails.segments`,
with the sector map as the fallback). Index-ETF options are HEDGES: counted in
their own bucket AND attributed to the segment they hedge
(`segment_hedge_etf`), so each segment shows long, hedge and net.

HONESTY RULES. A position whose delta or spot could not be obtained is a NAMED
GAP, never a zero: it is listed in `gaps` and excluded from the totals, and the
totals say how many positions they cover. Nothing here places an order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from .quant_engine.options.manage import parse_occ

MULT = 100
#: Default segment cap (RISK_GUARDRAILS §2 sector cap 40–45%); kv overrides.
DEFAULT_CAP_PCT = 45.0
#: Index ETFs whose options are hedges, not single-name exposure.
INDEX_ETFS = ("SPY", "QQQ", "IWM", "SMH", "IGV", "XSP", "XLK", "XLE", "XLF", "DIA")

DeltaFn = Callable[[str, str, str, float], Optional[float]]   # (root, expiry, right, strike) -> delta


@dataclass
class Leg:
    contract: str
    root: str
    kind: str                 # "stock" | "put" | "call"
    qty: float                # signed: shares, or contracts
    spot: Optional[float]
    delta: Optional[float]    # per unit; None = unknown
    delta_source: str         # "shares" | "chain" | "black_scholes(mark)" | "missing"
    expiry: Optional[str] = None
    strike: Optional[float] = None
    segment: str = "other"
    is_hedge: bool = False
    hedges_segment: Optional[str] = None

    @property
    def delta_dollars(self) -> Optional[float]:
        if self.spot is None or self.delta is None:
            return None
        mult = 1 if self.kind == "stock" else MULT
        return self.qty * self.delta * mult * self.spot


@dataclass
class ExposureConfig:
    segments: dict[str, list[str]] = field(default_factory=dict)        # segment -> symbols
    segment_hedge_etf: dict[str, str] = field(default_factory=dict)      # segment -> ETF
    sector_of: dict[str, str] = field(default_factory=dict)              # fallback: symbol -> sector
    cap_pct: float = DEFAULT_CAP_PCT

    @classmethod
    def from_kv(cls, kv: dict | None, sector_of: dict[str, str] | None = None) -> "ExposureConfig":
        kv = kv or {}
        cap = kv.get("sector_cap_pct")
        cap_pct = float(cap.get("high", DEFAULT_CAP_PCT)) if isinstance(cap, dict) else float(cap or DEFAULT_CAP_PCT)
        return cls(segments={k: [str(s).upper() for s in v] for k, v in (kv.get("segments") or {}).items()},
                   segment_hedge_etf={k: str(v).upper() for k, v in (kv.get("segment_hedge_etf") or {}).items()},
                   sector_of={k.upper(): v for k, v in (sector_of or {}).items()},
                   cap_pct=cap_pct)

    def segment_for(self, symbol: str) -> str:
        s = symbol.upper()
        for seg, syms in self.segments.items():
            if s in syms:
                return seg
        return self.sector_of.get(s) or "other"

    def hedged_segment(self, etf: str) -> Optional[str]:
        e = etf.upper()
        for seg, x in self.segment_hedge_etf.items():
            if x == e:
                return seg
        return None


def bs_delta_from_mark(*, mark: float | None, spot: float | None, strike: float, expiry: str,
                       right: str, today: date, rate: float = 0.04) -> Optional[float]:
    """Delta implied by the option's own mark when the chain gives none.
    Returns None rather than a guess when the mark is outside the no-arbitrage
    band or the inputs are missing."""
    if not mark or not spot or mark <= 0 or spot <= 0:
        return None
    try:
        from .quant_engine.options.black_scholes import BlackScholesPricer
        t = max((date.fromisoformat(expiry) - today).days, 0) / 365.0
        if t <= 0:
            # at expiry: intrinsic delta
            if right == "P":
                return -1.0 if spot < strike else 0.0
            return 1.0 if spot > strike else 0.0
        kind = "put" if right == "P" else "call"
        pricer = BlackScholesPricer(risk_free_rate=rate)
        sigma = pricer.implied_vol(mark, spot, strike, t, kind)
        if sigma is None:
            return None
        d = pricer.greeks(spot, strike, t, sigma, kind).delta
        return float(d) if math.isfinite(d) else None
    except Exception:  # noqa: BLE001 — a failed solve is a gap, not a number
        return None


def legs_from_positions(positions: list[dict], *, spots: dict[str, float | None],
                        delta_for: DeltaFn | None, cfg: ExposureConfig,
                        today: date | None = None) -> list[Leg]:
    """Flex OpenPosition rows -> legs with delta. `delta_for` is the live
    chain lookup (injected); when it returns None the delta is solved from
    the option's mark and labelled so."""
    today = today or date.today()
    legs: list[Leg] = []
    for p in positions:
        sym = str(p.get("symbol") or "")
        qty = float(p.get("quantity") or 0)
        if qty == 0:
            continue
        occ = parse_occ(sym)
        if not occ:
            root = sym.upper()
            legs.append(Leg(contract=sym, root=root, kind="stock", qty=qty, spot=spots.get(root),
                            delta=1.0, delta_source="shares", segment=cfg.segment_for(root)))
            continue
        root, expiry, right, strike = occ
        spot = spots.get(root)
        delta, src = None, "missing"
        if delta_for is not None:
            try:
                delta = delta_for(root, expiry, right, strike)
            except Exception:  # noqa: BLE001
                delta = None
            if delta is not None:
                src = "chain"
        if delta is None:
            mark = p.get("markPrice")
            delta = bs_delta_from_mark(mark=float(mark) if mark is not None else None, spot=spot,
                                       strike=strike, expiry=expiry, right=right, today=today)
            if delta is not None:
                src = "black_scholes(mark)"
        is_hedge = root in INDEX_ETFS
        legs.append(Leg(contract=sym, root=root, kind="put" if right == "P" else "call", qty=qty,
                        spot=spot, delta=delta, delta_source=src, expiry=expiry, strike=strike,
                        segment=("hedges" if is_hedge else cfg.segment_for(root)),
                        is_hedge=is_hedge, hedges_segment=cfg.hedged_segment(root) if is_hedge else None))
    return legs


def exposure(legs: list[Leg], *, cfg: ExposureConfig, nav_usd: float | None,
             gbp_per_usd: float | None = None) -> dict:
    """The book in dollar-delta: gross long, hedges, net, per segment, cap flags,
    and the gaps. Every total states how many legs it covers."""
    priced = [l for l in legs if l.delta_dollars is not None]
    gaps = [{"contract": l.contract, "symbol": l.root, "qty": l.qty,
             "missing": ("spot" if l.spot is None else "delta"),
             "note": f"no {'underlying price' if l.spot is None else 'delta from the chain, and no mark to solve one'} — excluded from the totals, not counted as zero"}
            for l in legs if l.delta_dollars is None]
    by_seg: dict[str, dict] = {}
    for l in priced:
        dd = float(l.delta_dollars)
        if l.is_hedge:
            seg = l.hedges_segment or "unassigned_hedge"
            d = by_seg.setdefault(seg, {"segment": seg, "long_usd": 0.0, "hedge_usd": 0.0, "legs": 0, "names": set()})
            d["hedge_usd"] += dd
            d["legs"] += 1
            d["names"].add(l.root)
        else:
            d = by_seg.setdefault(l.segment, {"segment": l.segment, "long_usd": 0.0, "hedge_usd": 0.0, "legs": 0, "names": set()})
            d["long_usd"] += dd
            d["legs"] += 1
            d["names"].add(l.root)
    gross_long = sum(d["long_usd"] for d in by_seg.values() if d["long_usd"] > 0)
    hedges = sum(d["hedge_usd"] for d in by_seg.values())
    net = sum(d["long_usd"] + d["hedge_usd"] for d in by_seg.values())
    segments = []
    for d in sorted(by_seg.values(), key=lambda x: -(x["long_usd"] + x["hedge_usd"])):
        net_seg = d["long_usd"] + d["hedge_usd"]
        share = (d["long_usd"] / gross_long * 100) if gross_long > 0 and d["long_usd"] > 0 else 0.0
        segments.append({
            "segment": d["segment"], "legs": d["legs"], "names": sorted(d["names"]),
            "long_usd": round(d["long_usd"], 0), "hedge_usd": round(d["hedge_usd"], 0),
            "net_usd": round(net_seg, 0),
            "share_of_gross_long_pct": round(share, 1),
            "net_pct_of_nav": round(net_seg / nav_usd * 100, 1) if nav_usd else None,
            "over_cap": bool(share > cfg.cap_pct),
            "hedge_cover_pct": round(-d["hedge_usd"] / d["long_usd"] * 100, 1) if d["long_usd"] > 0 and d["hedge_usd"] < 0 else 0.0,
        })
    flags = [f"{s['segment']} is {s['share_of_gross_long_pct']:.0f}% of gross long exposure, past the {cfg.cap_pct:.0f}% cap"
             for s in segments if s["over_cap"]]
    one_pct = net / 100.0
    return {
        "version": "LIVE_EXPOSURE_V1",
        "legs_total": len(legs), "legs_priced": len(priced), "legs_missing": len(gaps),
        "gross_long_usd": round(gross_long, 0), "hedges_usd": round(hedges, 0), "net_usd": round(net, 0),
        "net_pct_of_nav": round(net / nav_usd * 100, 1) if nav_usd else None,
        "one_pct_move_usd": round(one_pct, 0),
        "one_pct_move_gbp": round(one_pct * gbp_per_usd, 0) if gbp_per_usd else None,
        "cap_pct": cfg.cap_pct, "flags": flags, "segments": segments, "gaps": gaps,
        "delta_sources": {src: sum(1 for l in legs if l.delta_source == src)
                          for src in ("shares", "chain", "black_scholes(mark)", "missing")},
        "how": ("delta-dollars = qty × delta × multiplier × spot; shares delta 1; short puts count as long "
                "exposure, long puts and index-ETF options as hedges. Missing greeks are listed, not zeroed."),
    }
