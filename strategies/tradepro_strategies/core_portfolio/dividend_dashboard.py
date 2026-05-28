"""Dividend Dashboard — Track 2 module ③.

Per TradePro_Roadmap_May2026.docx §Track 2 module 3. For the
Compounder sleeve, dividend characteristics are first-class — a
quality compounder paying 3% with 12% CAGR for 15 years is the
SCHD / DVY / DGRO core trade. This module surfaces:

  - Current dividend yield + TTM dividend per share
  - 5-year dividend growth CAGR (from yfinance's 5-year history)
  - Payout ratio (sustainability check — > 80% is a red flag)
  - Consecutive years of dividend growth (Aristocrat ≥ 25, King ≥ 50)
  - Projected annual income from the user's position size — in £

Vocabulary: STRONG / STEADY / UNDER_PRESSURE / NONE — distinct from
Track 1's BUY/WAIT/AVOID. STRONG means yield + CAGR + payout all
support continued growth; UNDER_PRESSURE means coverage is shaky or
growth has stalled; NONE means a non-payer (common for tech /
small-caps — surface so it's clear, not silently zero).

Inputs:
  symbol            — ticker
  info              — yfinance.Ticker.info dict (test path); fetched live otherwise
  dividends_series  — pandas Series of historic dividend payments
                      (yfinance.Ticker.dividends); fetched live otherwise
  position_size_gbp — optional user holding size for projected income;
                      None → skip the income line

Pure function — no network calls when `info` and `dividends_series`
are provided. Behave coverage feeds synthetic dividend timeseries.

Deferred to v2:
  - Aristocrat / King flags need a curated list (or Morningstar paid feed)
  - DCA scheduler (planned monthly contributions) — Track 2 module 4 work
  - Per-position cost-basis-adjusted yield-on-cost
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

_log = logging.getLogger("tradepro.core_portfolio.dividend_dashboard")


Verdict = Literal["STRONG", "STEADY", "UNDER_PRESSURE", "NONE"]


@dataclass
class DividendDashboard:
    """Symbol-level dividend view. Maps 1:1 to the Track 2 dividend
    block on the Compounder card."""
    symbol: str
    verdict: Verdict
    current_yield_pct: float | None
    ttm_dps: float | None              # trailing-12-month dividend per share
    five_year_cagr_pct: float | None   # negative if dividend has been cut
    payout_ratio_pct: float | None     # > 80% suggests sustainability risk
    consecutive_growth_years: int | None
    projected_annual_income_gbp: float | None
    rationale: str
    source: str = "yfinance"

    def to_dict(self) -> dict:
        return {
            "symbol":                       self.symbol,
            "verdict":                      self.verdict,
            "current_yield_pct":            (round(self.current_yield_pct, 3)
                                              if self.current_yield_pct is not None else None),
            "ttm_dps":                      (round(self.ttm_dps, 4)
                                              if self.ttm_dps is not None else None),
            "five_year_cagr_pct":           (round(self.five_year_cagr_pct, 2)
                                              if self.five_year_cagr_pct is not None else None),
            "payout_ratio_pct":             (round(self.payout_ratio_pct, 2)
                                              if self.payout_ratio_pct is not None else None),
            "consecutive_growth_years":     self.consecutive_growth_years,
            "projected_annual_income_gbp":  (round(self.projected_annual_income_gbp, 2)
                                              if self.projected_annual_income_gbp is not None else None),
            "rationale":                    self.rationale,
            "source":                       self.source,
        }


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _annualise_dividends(dividends_series) -> dict[int, float]:
    """Group a pandas Series of dividend payments by calendar year and
    sum. Returns {year: total_dps}. Accepts None / empty input."""
    if dividends_series is None or len(dividends_series) == 0:
        return {}
    out: dict[int, float] = {}
    for ts, amount in dividends_series.items():
        if amount is None or amount != amount:
            continue
        try:
            yr = int(ts.year) if hasattr(ts, "year") else int(str(ts)[:4])
        except (ValueError, AttributeError):
            continue
        out[yr] = out.get(yr, 0.0) + float(amount)
    return out


def _five_year_cagr(annual: dict[int, float]) -> float | None:
    """Compound annual growth rate over the past 5 complete years.
    Returns None when we don't have a 5y window or the start year was
    zero-dividend (division would be undefined)."""
    if not annual:
        return None
    years = sorted(annual.keys())
    today = date.today().year
    # Use the most recent COMPLETED calendar year as the end point so
    # a half-year of 2026 payments doesn't read as a 50% drop.
    end_year = max(y for y in years if y <= today - 1) if any(y <= today - 1 for y in years) else None
    if end_year is None:
        return None
    start_year = end_year - 5
    end_dps = annual.get(end_year, 0.0)
    start_dps = annual.get(start_year, 0.0)
    if start_dps <= 0 or end_dps <= 0:
        return None
    ratio = end_dps / start_dps
    cagr = (ratio ** (1.0 / 5.0) - 1.0) * 100.0
    return cagr


def _consecutive_growth_years(annual: dict[int, float]) -> int:
    """Count consecutive calendar years (ending at the most recent
    COMPLETED year) where annual dividend total >= previous year.
    Equality counts as growth (a flat payer hasn't cut)."""
    if not annual:
        return 0
    years = sorted(annual.keys())
    today = date.today().year
    end_year = max((y for y in years if y <= today - 1), default=None)
    if end_year is None:
        return 0
    streak = 0
    prev = None
    for y in range(end_year, end_year - 30, -1):
        if y not in annual:
            break
        if prev is None:
            prev = annual[y]
            streak += 1
            continue
        if annual[y - 1 if False else y] is None:  # noqa — placeholder
            break
        # We're walking backwards; for streak we need to compare each
        # year to the year before it.
        prior = annual.get(y - 1)
        if prior is None:
            break
        if annual[y] + 1e-9 >= prior:
            streak += 1
            prev = annual[y]
            continue
        break
    return max(streak, 0)


def _classify(
    *,
    yield_pct: float | None,
    cagr_pct: float | None,
    payout_pct: float | None,
) -> tuple[Verdict, str]:
    """Verdict rules:

      NONE             yield is None or 0 — no dividend programme
      UNDER_PRESSURE   payout > 80% OR negative CAGR OR yield < 1% with weak CAGR
      STRONG           yield >= 2% AND CAGR >= 7% AND payout <= 70%
      STEADY           everything else (paying but not best-in-class)
    """
    if yield_pct is None or yield_pct < 0.1:
        return "NONE", "no dividend programme detected"
    if payout_pct is not None and payout_pct > 80:
        return ("UNDER_PRESSURE",
                f"payout ratio {payout_pct:.0f}% > 80% — coverage shaky")
    if cagr_pct is not None and cagr_pct < 0:
        return ("UNDER_PRESSURE",
                f"5y dividend CAGR {cagr_pct:.1f}% — payout has declined")
    if yield_pct < 1.0 and (cagr_pct is None or cagr_pct < 5):
        return ("UNDER_PRESSURE",
                f"yield {yield_pct:.2f}% with weak growth — neither income nor compounder")
    if (yield_pct >= 2.0
            and cagr_pct is not None and cagr_pct >= 7.0
            and (payout_pct is None or payout_pct <= 70)):
        return ("STRONG",
                f"yield {yield_pct:.2f}%, 5y CAGR {cagr_pct:.1f}%, "
                f"sustainable payout — core compounder profile")
    return ("STEADY",
            f"paying yield {yield_pct:.2f}%, growth/coverage adequate")


def _fetch(symbol: str) -> tuple[dict[str, Any] | None, Any]:
    try:
        import yfinance as yf
    except ImportError:
        _log.warning("yfinance not installed; cannot fetch %s", symbol)
        return None, None
    try:
        t = yf.Ticker(symbol)
        return (t.info or {}), t.dividends
    except Exception as e:  # noqa: BLE001
        _log.warning("yfinance fetch failed for %s: %s", symbol, e)
        return None, None


def compute_dividend_dashboard(
    symbol: str,
    *,
    info: dict[str, Any] | None = None,
    dividends_series: Any = None,
    position_size_gbp: float | None = None,
    fx_rate_gbpusd: float = 1.27,
) -> DividendDashboard:
    """Build the dashboard for `symbol`. Pass `info` + `dividends_series`
    for offline / test path; otherwise yfinance is consulted live.

    `position_size_gbp` is optional — when supplied, computes the
    projected annual dividend income in £ based on TTM DPS and the
    given FX rate."""
    if info is None and dividends_series is None:
        info, dividends_series = _fetch(symbol)
    info = info or {}

    yield_pct = _safe_float(info.get("dividendYield"))
    # yfinance returns dividendYield as a fraction (0.034 = 3.4%);
    # normalise to percent.
    if yield_pct is not None and yield_pct < 1.5:
        # Heuristic: yfinance sometimes returns the percent directly
        # (3.4) and sometimes the fraction (0.034). Values < 1.5 are
        # almost certainly the fraction form for any real stock.
        yield_pct = yield_pct * 100.0

    payout_pct = _safe_float(info.get("payoutRatio"))
    if payout_pct is not None and payout_pct < 5:
        payout_pct = payout_pct * 100.0

    annual = _annualise_dividends(dividends_series)
    cagr = _five_year_cagr(annual)
    consec = _consecutive_growth_years(annual)

    # TTM DPS — sum of the most recent ~12 months of payments. If we
    # have annual data, use the most recent complete year as a proxy.
    ttm_dps: float | None = None
    if annual:
        today = date.today().year
        latest_complete = max((y for y in annual if y <= today - 1), default=None)
        if latest_complete is not None:
            ttm_dps = annual[latest_complete]
        else:
            # Fall back to YTD if no complete year yet
            ttm_dps = annual.get(today)

    projected: float | None = None
    if (position_size_gbp is not None and position_size_gbp > 0
            and yield_pct is not None and yield_pct > 0):
        # Approximate via yield × position value. £ in → £ income out
        # so no FX conversion needed for the income figure itself.
        projected = position_size_gbp * (yield_pct / 100.0)

    verdict, rationale = _classify(
        yield_pct=yield_pct, cagr_pct=cagr, payout_pct=payout_pct,
    )

    return DividendDashboard(
        symbol=symbol.upper(),
        verdict=verdict,
        current_yield_pct=yield_pct,
        ttm_dps=ttm_dps,
        five_year_cagr_pct=cagr,
        payout_ratio_pct=payout_pct,
        consecutive_growth_years=consec,
        projected_annual_income_gbp=projected,
        rationale=rationale,
        source="yfinance",
    )
