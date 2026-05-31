"""Defined-risk options strategy builders (SRS §3.2) — SIGNALS ONLY.

Produces `Opportunity` objects (the legs + economics + Greeks) for DISPLAY;
no execution. Two tiers:
  Tier 1 — Bull Put Spread (directional, defined risk):
      trigger: underlying touches its 200-SMA AND IVR > 30
      build:   sell ~30Δ put, buy ~15Δ put (same expiry)
  Tier 2 — Iron Condor (vega / range, defined risk):
      trigger: IVR > 50
      build:   sell ~20Δ + buy ~10Δ on BOTH wings, ~45 DTE

Liquidity gate (SRS §3.1): if the ATM bid-ask > $0.05 AND > 2% of mid, the
chain is too wide → no signal. Everything is per-share; ×100 for a contract.
PROVISIONAL: economics are model/quote-derived off a free feed — triage,
not execution. See ROADMAP.
"""
from __future__ import annotations

from dataclasses import dataclass

from .black_scholes import BlackScholesPricer
from .chains import OptionChain, OptionQuote, select_by_abs_delta


@dataclass(frozen=True)
class Leg:
    action: str        # "SELL" | "BUY"
    kind: str          # "call" | "put"
    strike: float
    mid: float
    abs_delta: float


@dataclass(frozen=True)
class Opportunity:
    strategy: str            # "bull_put_spread" | "iron_condor"
    symbol: str
    expiry: str
    dte: int
    legs: list[Leg]
    net_credit: float        # premium collected per share (>0 = credit)
    max_loss: float          # per share (Strike width − credit)
    max_profit: float        # = net_credit
    breakevens: list[float]
    pop_estimate: float      # rough probability-of-profit (1 − short |delta|-ish)
    why: str


def liquidity_ok(chain: OptionChain, *, max_abs: float = 0.05, max_pct: float = 0.02) -> bool:
    """ATM spread must be tight: ≤ $0.05 OR ≤ 2% of mid (per SRS — banned
    only when BOTH are breached)."""
    atm = chain.atm_strike()
    near = [q for q in (chain.calls + chain.puts) if abs(q.strike - atm) < 1e-9 and q.mid > 0]
    if not near:
        return False
    q = min(near, key=lambda x: x.spread)
    return q.spread <= max_abs or (q.mid > 0 and q.spread / q.mid <= max_pct)


def _credit_spread(short: OptionQuote, long: OptionQuote) -> tuple[float, float, float]:
    """(net_credit, width, max_loss) for a vertical credit spread."""
    credit = max(0.0, short.mid - long.mid)
    width = abs(short.strike - long.strike)
    return credit, width, max(0.0, width - credit)


def build_bull_put_spread(
    chain: OptionChain, *, ivr: float | None, sma200_touch: bool,
    pricer: BlackScholesPricer | None = None,
) -> Opportunity | None:
    """Tier 1: sell ~30Δ put / buy ~15Δ put. Needs IVR>30 + a 200-SMA touch."""
    if ivr is None or ivr <= 30 or not sma200_touch:
        return None
    if not liquidity_ok(chain):
        return None
    pricer = pricer or BlackScholesPricer()
    t = chain.t_years
    short = select_by_abs_delta(chain.puts, 0.30, chain.spot, t, pricer)
    long = select_by_abs_delta(chain.puts, 0.15, chain.spot, t, pricer)
    if not short or not long or short.strike <= long.strike:
        return None
    credit, width, max_loss = _credit_spread(short, long)
    if credit <= 0:
        return None
    sd = abs(pricer.greeks(chain.spot, short.strike, t, max(short.iv, 1e-4), "put").delta)
    return Opportunity(
        strategy="bull_put_spread", symbol=chain.symbol, expiry=chain.expiry, dte=chain.dte,
        legs=[
            Leg("SELL", "put", short.strike, short.mid, sd),
            Leg("BUY", "put", long.strike, long.mid, abs(pricer.greeks(chain.spot, long.strike, t, max(long.iv, 1e-4), "put").delta)),
        ],
        net_credit=credit, max_loss=max_loss, max_profit=credit,
        breakevens=[short.strike - credit],
        pop_estimate=round(1.0 - sd, 3),
        why=f"IVR {ivr:.0f}>30 + 200-SMA touch; sell {sd:.2f}Δ / buy ~0.15Δ put, width {width:.1f}",
    )


def build_iron_condor(
    chain: OptionChain, *, ivr: float | None,
    pricer: BlackScholesPricer | None = None,
) -> Opportunity | None:
    """Tier 2: sell ~20Δ / buy ~10Δ on both wings. Needs IVR>50."""
    if ivr is None or ivr <= 50:
        return None
    if not liquidity_ok(chain):
        return None
    pricer = pricer or BlackScholesPricer()
    t = chain.t_years
    sp = select_by_abs_delta(chain.puts, 0.20, chain.spot, t, pricer)
    lp = select_by_abs_delta(chain.puts, 0.10, chain.spot, t, pricer)
    sc = select_by_abs_delta(chain.calls, 0.20, chain.spot, t, pricer)
    lc = select_by_abs_delta(chain.calls, 0.10, chain.spot, t, pricer)
    if not all((sp, lp, sc, lc)):
        return None
    if not (lp.strike < sp.strike < sc.strike < lc.strike):
        return None
    put_credit, put_w, _ = _credit_spread(sp, lp)
    call_credit, call_w, _ = _credit_spread(sc, lc)
    credit = put_credit + call_credit
    if credit <= 0:
        return None
    # Max loss on an IC = wider wing width − total credit.
    max_loss = max(0.0, max(put_w, call_w) - credit)
    sd_put = abs(pricer.greeks(chain.spot, sp.strike, t, max(sp.iv, 1e-4), "put").delta)
    return Opportunity(
        strategy="iron_condor", symbol=chain.symbol, expiry=chain.expiry, dte=chain.dte,
        legs=[
            Leg("SELL", "put", sp.strike, sp.mid, sd_put),
            Leg("BUY", "put", lp.strike, lp.mid, abs(pricer.greeks(chain.spot, lp.strike, t, max(lp.iv, 1e-4), "put").delta)),
            Leg("SELL", "call", sc.strike, sc.mid, abs(pricer.greeks(chain.spot, sc.strike, t, max(sc.iv, 1e-4), "call").delta)),
            Leg("BUY", "call", lc.strike, lc.mid, abs(pricer.greeks(chain.spot, lc.strike, t, max(lc.iv, 1e-4), "call").delta)),
        ],
        net_credit=credit, max_loss=max_loss, max_profit=credit,
        breakevens=[sp.strike - credit, sc.strike + credit],
        pop_estimate=round(1.0 - 2.0 * sd_put, 3),
        why=f"IVR {ivr:.0f}>50; sell ~0.20Δ / buy ~0.10Δ both wings, ~{chain.dte}DTE",
    )


__all__ = ["Opportunity", "Leg", "liquidity_ok", "build_bull_put_spread", "build_iron_condor"]
