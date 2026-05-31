"""BlackScholesPricer — fair value + Greeks for European options.

P0 of the options framework (SRS §3.2 "Fallback Pricing"): an independent
Black-Scholes pricer used to (a) compute Greeks (Δ/Γ/Θ/Vega/Rho) when the
data source doesn't pre-supply them, and (b) sanity-check a broker/source
quoted mid against a model fair value so a stale or fat-fingered chain
can't drive a signal.

Deliberately ZERO third-party deps — uses `math.erf` for the normal CDF,
so it loads anywhere the rest of the package does (no scipy). ETF options
are American, but Black-Scholes is the standard model approximation for
liquid, near-ATM, non-dividend-event index ETFs and is the right tool for
the "verify fair value + derive Greeks" job here (not for early-exercise
edge pricing).

Conventions:
  - T is time to expiry in YEARS (use days/365).
  - sigma, r, q are annualised decimals (0.20 = 20% vol, 0.05 = 5% rate).
  - Vega is per 1.00 (100%) vol move; divide by 100 for "per 1% vol".
  - Theta is per YEAR; divide by 365 for "per calendar day".
  - All prices/Greeks are per 1 unit of the underlying (×100 for a
    standard US equity-option contract multiplier at the call site).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["call", "put"]

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — no scipy needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


@dataclass(frozen=True)
class Greeks:
    """Per-unit option price + Greeks. Multiply by the contract
    multiplier (typically 100) at the call site for $-per-contract."""
    price: float
    delta: float
    gamma: float
    theta_per_year: float
    vega_per_1pct: float   # already scaled to a 1% vol move (the trader unit)
    rho_per_1pct: float    # per 1% rate move

    @property
    def theta_per_day(self) -> float:
        return self.theta_per_year / 365.0


class BlackScholesPricer:
    """Stateless Black-Scholes-Merton pricer (continuous dividend yield q)."""

    def __init__(self, risk_free_rate: float = 0.04, dividend_yield: float = 0.0) -> None:
        self.r = float(risk_free_rate)
        self.q = float(dividend_yield)

    # ------------------------------------------------------------------ #
    def _d1_d2(self, spot: float, strike: float, t: float, sigma: float) -> tuple[float, float]:
        # Guard the degenerate inputs that would divide-by-zero.
        vol_sqrt_t = sigma * math.sqrt(t)
        d1 = (math.log(spot / strike) + (self.r - self.q + 0.5 * sigma * sigma) * t) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t
        return d1, d2

    def price(self, spot: float, strike: float, t: float, sigma: float, kind: OptionType) -> float:
        return self.greeks(spot, strike, t, sigma, kind).price

    def greeks(
        self, spot: float, strike: float, t: float, sigma: float, kind: OptionType,
    ) -> Greeks:
        """Full price + Greeks. Falls back to intrinsic value (zero
        Greeks) at/near expiry or with non-positive vol, so callers never
        hit a NaN on a stale or zero-DTE row."""
        if t <= 1e-9 or sigma <= 1e-9 or spot <= 0 or strike <= 0:
            intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
            return Greeks(price=intrinsic, delta=0.0, gamma=0.0,
                          theta_per_year=0.0, vega_per_1pct=0.0, rho_per_1pct=0.0)

        d1, d2 = self._d1_d2(spot, strike, t, sigma)
        disc_r = math.exp(-self.r * t)
        disc_q = math.exp(-self.q * t)
        pdf_d1 = _norm_pdf(d1)

        if kind == "call":
            price = spot * disc_q * _norm_cdf(d1) - strike * disc_r * _norm_cdf(d2)
            delta = disc_q * _norm_cdf(d1)
            theta = (
                -(spot * disc_q * pdf_d1 * sigma) / (2.0 * math.sqrt(t))
                - self.r * strike * disc_r * _norm_cdf(d2)
                + self.q * spot * disc_q * _norm_cdf(d1)
            )
            rho = strike * t * disc_r * _norm_cdf(d2)
        else:  # put
            price = strike * disc_r * _norm_cdf(-d2) - spot * disc_q * _norm_cdf(-d1)
            delta = disc_q * (_norm_cdf(d1) - 1.0)
            theta = (
                -(spot * disc_q * pdf_d1 * sigma) / (2.0 * math.sqrt(t))
                + self.r * strike * disc_r * _norm_cdf(-d2)
                - self.q * spot * disc_q * _norm_cdf(-d1)
            )
            rho = -strike * t * disc_r * _norm_cdf(-d2)

        gamma = (disc_q * pdf_d1) / (spot * sigma * math.sqrt(t))
        vega = spot * disc_q * pdf_d1 * math.sqrt(t)   # per 1.00 vol
        return Greeks(
            price=price,
            delta=delta,
            gamma=gamma,
            theta_per_year=theta,
            vega_per_1pct=vega / 100.0,
            rho_per_1pct=rho / 100.0,
        )

    def implied_vol(
        self, market_price: float, spot: float, strike: float, t: float, kind: OptionType,
        *, lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6, max_iter: int = 100,
    ) -> float | None:
        """Back out implied volatility from a market price by bisection.
        Robust (no derivative blow-ups near the wings, unlike Newton).
        Returns None if the price is outside the no-arbitrage band."""
        if market_price <= 0 or t <= 0:
            return None
        # No-arb bounds: price must sit between intrinsic and the spot-ish cap.
        intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
        if market_price < intrinsic - tol:
            return None
        p_lo = self.price(spot, strike, t, lo, kind)
        p_hi = self.price(spot, strike, t, hi, kind)
        if not (p_lo - tol <= market_price <= p_hi + tol):
            return None
        for _ in range(max_iter):
            mid = 0.5 * (lo + hi)
            p_mid = self.price(spot, strike, t, mid, kind)
            if abs(p_mid - market_price) < tol:
                return mid
            if p_mid < market_price:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def implied_vol_rank(current_iv: float, iv_history: list[float]) -> float | None:
    """IV Rank (IVR) per SRS §3.2 triggers: where today's IV sits in its
    own trailing range, 0–100. IVR = (IV - min) / (max - min) × 100 over
    the lookback (typically ~252 trading days). Returns None without
    enough history. (Distinct from IV Percentile — this is the range
    position the SRS's IVR>30 / >50 thresholds refer to.)"""
    hist = [v for v in iv_history if v is not None]
    if len(hist) < 2:
        return None
    lo, hi = min(hist), max(hist)
    if hi - lo < 1e-9:
        return 0.0
    return max(0.0, min(100.0, (current_iv - lo) / (hi - lo) * 100.0))


__all__ = ["BlackScholesPricer", "Greeks", "OptionType", "implied_vol_rank"]
