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
    # Valuation (single-stock-flavoured — usually null on ETFs).
    # forward_pe is forward 12m EPS, trailing_pe is last 12m. Lower
    # = cheaper, basket-relative. Replaces dividend yield as the
    # primary cross-sectional valuation lens since dividend yield
    # gives garbage on growth names that don't pay much (NVDA, AMZN).
    forward_pe: float | None
    trailing_pe: float | None
    # Phase G v2 stock-quality floor — debt/equity <1.5 and FCF >0
    # are the cheap-but-fast signals from Yahoo's info dict that
    # filter most value traps without needing a full income-stmt parse.
    debt_to_equity: float | None
    free_cashflow: float | None
    # Bond ETF flavour
    yield_to_maturity_pct: float | None
    duration_years: float | None
    # Composition
    top_holdings: list[TopHolding]
    sector_weights: dict[str, float]    # name → weight as fraction (0.18 = 18%)
    # Total number of underlying holdings in the fund. Used by the
    # passive-horizon scorer per TRADEPRO-SPEC-001 §4.3 — broad
    # diversification (>200 holdings) earns 2 points; <50 earns 0.
    # Stocks (legal_type == "EQUITY") naturally have n_holdings == 1.
    n_holdings: int | None = None
    summary: str | None = None          # one-paragraph description
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
            "forward_pe": self.forward_pe,
            "trailing_pe": self.trailing_pe,
            "debt_to_equity": self.debt_to_equity,
            "free_cashflow": self.free_cashflow,
            "yield_to_maturity_pct": self.yield_to_maturity_pct,
            "duration_years": self.duration_years,
            "top_holdings": [h.to_dict() for h in self.top_holdings],
            "sector_weights": self.sector_weights,
            "n_holdings": self.n_holdings,
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
        forward_pe=None, trailing_pe=None,
        debt_to_equity=None, free_cashflow=None,
        yield_to_maturity_pct=None, duration_years=None,
        top_holdings=[], sector_weights={}, n_holdings=None, summary=None,
    )


def _safe_float(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _safe_int(x) -> int | None:
    """Same as _safe_float but for integer-typed Yahoo fields. Yahoo
    occasionally returns ints as strings or floats; normalise to int
    when the value is sensibly representable, else None."""
    f = _safe_float(x)
    if f is None or f < 0:
        return None
    return int(f)


def _funds_data_holdings_count(symbol: str) -> int | None:
    """Try to extract the fund's true basket size (e.g. ~700 for VLUE).
    yfinance's `funds_data.equity_holdings` is sometimes the full table
    and sometimes a head-N cap; we can't always tell, so we only return
    a value when it's CLEARLY the full table — i.e. > 12 rows. Anything
    in the 6-10 region looks like the head cap and gets None instead
    (better to omit than mislead the passive scorer with n_holdings=6
    on a 700-symbol ETF, the bug that shipped in the first cut)."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        funds_data = getattr(t, "funds_data", None)
        if funds_data is None:
            return None
        for attr in ("equity_holdings", "asset_classes"):
            df = getattr(funds_data, attr, None)
            if df is not None:
                n_rows = int(getattr(df, "shape", (0,))[0])
                # Head-cap detection: any value ≤ 12 is suspicious
                # because yfinance's top_holdings caps at 10 and similar
                # caps exist for legacy fields. A real fund basket is
                # usually 30+ holdings.
                if n_rows > 12:
                    return n_rows
    except Exception:  # noqa: BLE001
        return None
    return None


def _frac_to_pct(x) -> float | None:
    """Yahoo reports rates as decimal fractions (0.018 = 1.8%) but
    occasionally as already-percent (1.8 = 1.8%). Heuristic:

    - abs(f) < 0.3   → fraction (typical 0.0123 → 1.23%)
    - abs(f) ≥ 0.3   → already a percent (1.46 stays 1.46%)

    No upper bound here because this helper is also used for multi-
    year returns where ±100% is normal (5y CAGR on a tech stock,
    drawdown on a crash universe). For yields specifically, use
    `_yield_pct` which adds a sanity cap.

    The previous 1.5 cutoff caused 1.46% (already a percent) to be
    multiplied to 146% — the SIZE / QUAL yield bug from May 2026."""
    f = _safe_float(x)
    if f is None:
        return None
    return f * 100.0 if abs(f) < 0.3 else f


# Cap for "yield in percent" — anything above this is almost
# certainly wrong upstream data (Yahoo occasionally returns 91 / 146
# for SIZE / QUAL when the dividend rate column gets confused with
# yield). Above the cap we null the field rather than ship a fake
# 146% yield that scores +1 on the passive horizon.
_MAX_PLAUSIBLE_YIELD_PCT = 25.0


def _yield_pct(x) -> float | None:
    """Yield-specific wrapper around _frac_to_pct that nulls out
    obviously corrupted values (>25%). No realistic fund yield is
    higher than ~12% in current markets; a 90+% reading means
    Yahoo confused dividend-rate-per-share with yield."""
    pct = _frac_to_pct(x)
    if pct is None:
        return None
    if abs(pct) > _MAX_PLAUSIBLE_YIELD_PCT:
        return None
    return pct


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

    # n_holdings: total underlying constituents in the fund. Used by
    # the passive-horizon scorer (TRADEPRO-SPEC-001 §4.3 — diversification).
    # Resolution order: explicit Yahoo `holdingsCount` field → top-level
    # holdings list count from funds_data (when caller didn't fetch top
    # holdings, the API often still reports the total count) → 1 when
    # the asset is an individual equity → None for unknown.
    legal_type = (info.get("legalType") or info.get("quoteType") or "").upper()
    n_holdings = _safe_int(
        info.get("holdingsCount")
        or info.get("totalHoldings")
        or info.get("numberOfHoldings")
    )
    if n_holdings is None:
        n_holdings = _funds_data_holdings_count(symbol)
    if n_holdings is None and legal_type in {"EQUITY", "COMMON STOCK"}:
        n_holdings = 1

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
        dividend_yield_pct=_yield_pct(
            info.get("trailingAnnualDividendYield") or info.get("dividendYield")
        ),
        distribution_yield_pct=_yield_pct(info.get("yield")),
        ytd_return_pct=_frac_to_pct(info.get("ytdReturn")),
        three_year_return_pct=_frac_to_pct(info.get("threeYearAverageReturn")),
        five_year_return_pct=_frac_to_pct(info.get("fiveYearAverageReturn")),
        # P/E is a raw ratio in Yahoo's payload — do NOT run through
        # _frac_to_pct (which would mis-scale a 28× P/E to 28%).
        forward_pe=_safe_float(info.get("forwardPE")),
        trailing_pe=_safe_float(info.get("trailingPE")),
        # Phase G stock-quality floor inputs. Yahoo's debtToEquity is
        # already in 1.0-units (e.g. 1.46 = 146%-of-equity in debt);
        # we keep that scale and rule on it directly. freeCashflow is
        # absolute USD (or symbol's reporting currency).
        debt_to_equity=_safe_float(info.get("debtToEquity")),
        free_cashflow=_safe_float(info.get("freeCashflow")),
        yield_to_maturity_pct=_yield_pct(info.get("yieldToMaturity")),
        duration_years=_safe_float(info.get("duration") or info.get("modifiedDuration")),
        top_holdings=holdings,
        sector_weights=_extract_sector_weights(info),
        n_holdings=n_holdings,
        summary=info.get("longBusinessSummary"),
    )
