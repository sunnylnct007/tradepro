"""ETF fundamentals: yield, expense ratio, AUM, top holdings, sector mix.

Pulled from Yahoo's quote-summary blob (yfinance.Ticker.info / .funds_data
when available). Slow-moving, weekly refresh would be plenty — but we
piggy-back on the comparator's per-run fetch so it's always at most as
stale as the BUY/WAIT/AVOID verdict on the row.

Different ETFs expose different fields:
- Equity ETFs:       expense_ratio, aum, top_holdings (with weights),
                     sector_weights, fund_family, inception_date.
- Bond ETFs:         expense_ratio, aum, yield_to_maturity, duration.
- Commodity ETFs:    expense_ratio, aum, holding (single underlying).

Anything missing is left null — the renderer should hide rows that
don't apply to a given ETF rather than show 'N/A' everywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class TopHolding:
    symbol: str | None
    name: str
    weight_pct: float | None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "weight_pct": self.weight_pct,
        }


@dataclass
class Fundamentals:
    symbol: str
    fetched_at: str
    fund_family: str | None
    category: str | None
    legal_type: str | None              # 'Exchange Traded Fund', 'Equity', etc.
    inception_date: str | None          # ISO YYYY-MM-DD when known
    # Costs + flows
    expense_ratio_pct: float | None     # e.g. 0.03 for VOO (0.03% per year)
    aum_usd: float | None               # total assets under management
    # Returns / yields
    dividend_yield_pct: float | None    # trailing 12m
    distribution_yield_pct: float | None  # ETF-specific
    ytd_return_pct: float | None
    three_year_return_pct: float | None
    five_year_return_pct: float | None
    # Bond ETF flavour
    yield_to_maturity_pct: float | None
    duration_years: float | None
    # Composition
    top_holdings: list[TopHolding]
    sector_weights: dict[str, float]    # name → weight as fraction (0.18 = 18%)
    summary: str | None                 # one-paragraph description
    source: str = "yahoo"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "fetched_at": self.fetched_at,
            "fund_family": self.fund_family,
            "category": self.category,
            "legal_type": self.legal_type,
            "inception_date": self.inception_date,
            "expense_ratio_pct": self.expense_ratio_pct,
            "aum_usd": self.aum_usd,
            "dividend_yield_pct": self.dividend_yield_pct,
            "distribution_yield_pct": self.distribution_yield_pct,
            "ytd_return_pct": self.ytd_return_pct,
            "three_year_return_pct": self.three_year_return_pct,
            "five_year_return_pct": self.five_year_return_pct,
            "yield_to_maturity_pct": self.yield_to_maturity_pct,
            "duration_years": self.duration_years,
            "top_holdings": [h.to_dict() for h in self.top_holdings],
            "sector_weights": self.sector_weights,
            "summary": self.summary,
            "source": self.source,
        }


def _empty(symbol: str) -> Fundamentals:
    return Fundamentals(
        symbol=symbol,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        fund_family=None, category=None, legal_type=None, inception_date=None,
        expense_ratio_pct=None, aum_usd=None,
        dividend_yield_pct=None, distribution_yield_pct=None,
        ytd_return_pct=None, three_year_return_pct=None, five_year_return_pct=None,
        yield_to_maturity_pct=None, duration_years=None,
        top_holdings=[], sector_weights={}, summary=None,
    )


def _safe_float(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _frac_to_pct(x) -> float | None:
    """Yahoo reports yields and ratios as decimal fractions (0.018 = 1.8%).
    Convert to percentage. Some legacy fields are already in percent — heuristic:
    if abs(x) > 1.5 we assume it's already a percent."""
    f = _safe_float(x)
    if f is None:
        return None
    return f * 100.0 if abs(f) <= 1.5 else f


def _inception_iso(epoch) -> str | None:
    """Yahoo's fundInceptionDate is a Unix timestamp."""
    f = _safe_float(epoch)
    if f is None or f <= 0:
        return None
    try:
        return datetime.fromtimestamp(int(f), tz=timezone.utc).date().isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _extract_top_holdings_from_info(info: dict) -> list[TopHolding]:
    """Older yfinance versions exposed top holdings under various info keys.
    First non-empty list wins. Newer yfinance has moved this to
    Ticker.funds_data — see _holdings_via_funds_data."""
    raw = (
        info.get("holdings")
        or info.get("topHoldings")
        or info.get("equityHoldings")
        or []
    )
    out: list[TopHolding] = []
    if isinstance(raw, list):
        for item in raw[:10]:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol") or item.get("ticker")
            name = item.get("holdingName") or item.get("name") or sym or "—"
            weight = _safe_float(item.get("holdingPercent") or item.get("weight"))
            if weight is not None and abs(weight) <= 1.5:
                weight = weight * 100.0
            out.append(TopHolding(symbol=sym, name=name, weight_pct=weight))
    return out


def _holdings_via_funds_data(symbol: str) -> list[TopHolding]:
    """Newer yfinance (>= 0.2.40) exposes ETF/MF holdings via
    Ticker.funds_data.top_holdings — a small DataFrame keyed by symbol
    with a 'Holding Percent' column. Fall back when info doesn't have
    them (which is most ETFs in current yfinance)."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        # funds_data is itself best-effort; symbol may not be a fund.
        funds_data = getattr(t, "funds_data", None)
        if funds_data is None:
            return []
        df = funds_data.top_holdings
        if df is None or df.empty:
            return []
    except Exception:  # noqa: BLE001
        return []

    out: list[TopHolding] = []
    try:
        # Index is the holding's symbol; columns vary across versions but
        # usually include 'Name' / 'Holding Name' and a percent column.
        for sym, row in df.head(10).iterrows():
            row_dict = row.to_dict() if hasattr(row, "to_dict") else dict(row)
            name = (
                row_dict.get("Name")
                or row_dict.get("Holding Name")
                or row_dict.get("holdingName")
                or sym
            )
            weight = _safe_float(
                row_dict.get("Holding Percent")
                or row_dict.get("Weight")
                or row_dict.get("holdingPercent")
            )
            if weight is not None and abs(weight) <= 1.5:
                weight = weight * 100.0
            out.append(TopHolding(symbol=str(sym), name=str(name), weight_pct=weight))
    except Exception:  # noqa: BLE001
        return []
    return out


def _extract_sector_weights(info: dict) -> dict[str, float]:
    """Yahoo's sectorWeightings is either a dict {sector: pct} or a list of
    single-key dicts [{tech: 0.32}, {healthcare: 0.18}, ...]."""
    raw = info.get("sectorWeightings")
    out: dict[str, float] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                for k, v in entry.items():
                    f = _safe_float(v)
                    if f is not None:
                        out[k] = f
    elif isinstance(raw, dict):
        for k, v in raw.items():
            f = _safe_float(v)
            if f is not None:
                out[k] = f
    return out


def fetch_fundamentals(symbol: str, info: dict | None = None) -> Fundamentals:
    """Best-effort. Returns an empty record on any failure.

    `info` may be passed in if the caller already fetched the Yahoo quote
    summary (e.g. external_consensus.py shares the same call).
    """
    if info is None:
        # Lazy import — avoids a hard dep on external_consensus when only
        # fundamentals is needed.
        from .external_consensus import _fetch_info
        info = _fetch_info(symbol)
    if info is None:
        return _empty(symbol)

    # Top holdings: try the inline info dict first (older yfinance), fall
    # back to the newer funds_data API.
    holdings = _extract_top_holdings_from_info(info)
    if not holdings:
        holdings = _holdings_via_funds_data(symbol)

    return Fundamentals(
        symbol=symbol,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        fund_family=info.get("fundFamily") or info.get("fund_family"),
        category=info.get("category"),
        legal_type=info.get("legalType") or info.get("quoteType"),
        inception_date=_inception_iso(info.get("fundInceptionDate")),
        # Yahoo returns expense ratio in percent already (0.03 means 0.03%
        # for VOO, not 3%). Don't apply the fraction → percent transform
        # we use for yields.
        expense_ratio_pct=_safe_float(
            info.get("netExpenseRatio") or info.get("expenseRatio") or info.get("annualReportExpenseRatio")
        ),
        aum_usd=_safe_float(info.get("totalAssets") or info.get("netAssets")),
        dividend_yield_pct=_frac_to_pct(
            info.get("trailingAnnualDividendYield") or info.get("dividendYield")
        ),
        distribution_yield_pct=_frac_to_pct(info.get("yield")),
        ytd_return_pct=_frac_to_pct(info.get("ytdReturn")),
        three_year_return_pct=_frac_to_pct(info.get("threeYearAverageReturn")),
        five_year_return_pct=_frac_to_pct(info.get("fiveYearAverageReturn")),
        yield_to_maturity_pct=_frac_to_pct(info.get("yieldToMaturity")),
        duration_years=_safe_float(info.get("duration") or info.get("modifiedDuration")),
        top_holdings=holdings,
        sector_weights=_extract_sector_weights(info),
        summary=info.get("longBusinessSummary"),
    )
