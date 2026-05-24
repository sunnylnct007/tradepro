"""Long-term fundamental analysis engine.

Fetches multi-year income statement, balance sheet, and cash-flow data
from yfinance and computes trend metrics suitable for long-term investment
research across *any* sector.

What yfinance provides (reliably)
──────────────────────────────────
  income_stmt   : Total Revenue, Gross Profit, Operating Income,
                  Net Income, Interest Expense, Normalized EBITDA
  balance_sheet : Total Assets, Stockholders Equity (or Common Stock Equity),
                  Long Term Debt, Current Assets, Current Liabilities, Cash
  cashflow      : Operating Cash Flow, Capital Expenditure, Free Cash Flow
  Ticker.info   : forward_pe, trailing_pe, price_to_book, return_on_equity,
                  profit_margins, beta, market_cap, sector, industry

Known data gaps per sector
──────────────────────────
  Banking       : NIM, GNPA/NNPA, CASA ratio, CAR, PCR — only in SEBI/RBI
                  filings and screener.in; not in yfinance.
  IT services   : TCV / deal wins, headcount, attrition, utilisation % — not
                  in yfinance.
  Pharma        : FDA pipeline status, ANDA filings, R&D pipeline — not
                  in yfinance (R&D spend IS available in income_stmt).
  Indian stocks : Use SYMBOL.NS tickers for INR-denominated data. USD ADRs
                  include FX drag and may reflect merger / dilution events.

Entry point: :func:`analyse_long_term`.
All helpers accept DataFrames so they can be unit-tested without network.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector templates
# ---------------------------------------------------------------------------

#: Per-sector metadata: which KPIs are most diagnostic, which are missing,
#: and what alternative data sources a human analyst would consult.
SECTOR_TEMPLATES: dict[str, dict] = {
    "banking": {
        "label": "Banking / Financial Services",
        "priority_kpis": [
            "revenue_cagr_3y", "net_margin_pct_latest", "roe_pct_latest",
            "net_margin_trend", "debt_equity_latest",
        ],
        "yfinance_gaps": [
            "NIM (Net Interest Margin) — use screener.in / annual report",
            "GNPA / NNPA ratio — use RBI / SEBI filings",
            "CASA ratio — use investor presentations",
            "Capital Adequacy Ratio (CAR) — use annual report Pillar 3",
            "Provision Coverage Ratio (PCR) — use annual report",
        ],
        "analyst_note": (
            "For Indian banks (e.g. HDFCBANK.NS, ICICIBANK.NS), the most "
            "important metrics — NIM, GNPA, CASA, CAR — are not available "
            "via yfinance. Cross-reference screener.in or the bank's "
            "investor-relations page. ROE > 15% and GNPA < 2% are "
            "typical quality thresholds."
        ),
    },
    "technology": {
        "label": "Technology / IT Services",
        "priority_kpis": [
            "revenue_cagr_3y", "revenue_cagr_5y", "op_margin_pct_latest",
            "op_margin_trend", "fcf_conversion_latest", "roe_pct_latest",
        ],
        "yfinance_gaps": [
            "TCV / deal wins — use earnings call transcripts",
            "Headcount / attrition rate — use annual report",
            "Revenue utilisation % — use company filings",
        ],
        "analyst_note": (
            "For IT services (TCS.NS, INFY, WIPRO.NS, HCL.NS), revenue "
            "growth + operating margin trajectory are the primary quality "
            "signals. EBIT margin > 20% and FCF conversion > 80% are "
            "typical of tier-1 Indian IT."
        ),
    },
    "pharma": {
        "label": "Healthcare / Pharmaceuticals",
        "priority_kpis": [
            "revenue_cagr_3y", "gross_margin_pct_latest",
            "rd_intensity_pct_latest", "fcf_conversion_latest",
        ],
        "yfinance_gaps": [
            "FDA pipeline / ANDA filings — use company pipeline page",
            "Biosimilar approvals — use earnings call transcripts",
            "Patent cliff schedule — use annual report",
        ],
        "analyst_note": (
            "R&D intensity (R&D / Revenue) and gross margin are the "
            "primary quality signals. R&D% > 8% and gross margin > 60% "
            "are typical for branded pharma. Generic-focused players "
            "run lower margins with higher volume."
        ),
    },
    "energy": {
        "label": "Energy",
        "priority_kpis": [
            "revenue_cagr_3y", "op_margin_pct_latest",
            "fcf_conversion_latest", "debt_equity_latest",
        ],
        "yfinance_gaps": [
            "Reserve life index — use annual report / 10-K",
            "Production costs per BOE — use operational filings",
        ],
        "analyst_note": (
            "FCF yield and D/E are critical cyclical indicators. "
            "Compare across commodity price cycles — a 5-year trend "
            "obscures single-year commodity spike effects."
        ),
    },
    "consumer_cyclical": {
        "label": "Consumer Cyclical",
        "priority_kpis": [
            "revenue_cagr_3y", "gross_margin_pct_latest",
            "op_margin_trend", "debt_equity_latest",
        ],
        "yfinance_gaps": [
            "Same-store sales growth (SSSG) — use earnings releases",
            "Store count / unit economics — use investor presentations",
        ],
        "analyst_note": (
            "SSSG is the primary demand quality signal; not in yfinance. "
            "Gross margin stability across cycles separates pricing-power "
            "brands from commodity retailers."
        ),
    },
    "default": {
        "label": "General / Mixed Sector",
        "priority_kpis": [
            "revenue_cagr_3y", "revenue_cagr_5y",
            "op_margin_pct_latest", "op_margin_trend",
            "net_margin_pct_latest", "roe_pct_latest",
            "fcf_conversion_latest", "debt_equity_latest",
        ],
        "yfinance_gaps": [],
        "analyst_note": (
            "Universal quality signals: revenue CAGR > 10%, operating "
            "margin expanding, ROE > 15%, FCF conversion > 70%, D/E < 1."
        ),
    },
}

# ---------------------------------------------------------------------------
# Known peers for comparison (extensible — add more as needed)
# ---------------------------------------------------------------------------

KNOWN_PEERS: dict[str, list[str]] = {
    # US mega-cap tech
    "AAPL":  ["MSFT", "GOOG", "META"],
    "MSFT":  ["AAPL", "GOOG", "CRM"],
    "NVDA":  ["AMD", "INTC", "AVGO"],
    "AMD":   ["NVDA", "INTC", "QCOM"],
    # US banks
    "JPM":   ["BAC", "GS", "MS", "WFC"],
    "BAC":   ["JPM", "WFC", "C"],
    "GS":    ["MS", "JPM", "C"],
    # US pharma
    "JNJ":   ["PFE", "ABBV", "MRK"],
    "PFE":   ["JNJ", "ABBV", "MRK", "BMY"],
    # Indian IT (NSE tickers)
    "TCS.NS":   ["INFY.NS", "WIPRO.NS", "HCLTECH.NS"],
    "INFY.NS":  ["TCS.NS", "WIPRO.NS", "HCLTECH.NS"],
    "WIPRO.NS": ["TCS.NS", "INFY.NS", "HCLTECH.NS"],
    # Indian banks (NSE tickers)
    "HDFCBANK.NS": ["ICICIBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
    "ICICIBANK.NS":["HDFCBANK.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
    # ADR versions (USD-denominated — include FX drag warning)
    "HDB":   ["IBN", "HDFC"],   # HDFC Bank ADR
    "IBN":   ["HDB", "HDFC"],   # ICICI Bank ADR
    "INFY":  ["WIT", "CTSH"],   # Infosys USD ADR
    "WIT":   ["INFY", "CTSH"],  # Wipro USD ADR
    # UK banks
    "HSBA.L": ["LLOY.L", "BARC.L", "NWG.L"],
}


# ---------------------------------------------------------------------------
# Pure-function helpers (testable without network)
# ---------------------------------------------------------------------------

def _safe_float(x: Any) -> float | None:
    """Return float or None — never NaN / inf."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _row(df: Any, *candidates: str) -> "list[float]":
    """Extract a row from a yfinance DataFrame trying multiple candidate
    row names (yfinance uses different names across versions / regions).
    Returns a list of *non-None* floats ordered most-recent-first."""
    if df is None or getattr(df, "empty", True):
        return []
    for name in candidates:
        if name in df.index:
            vals = [_safe_float(v) for v in df.loc[name]]
            return [v for v in vals if v is not None]
    return []


def compute_cagr(values: list[float], years: int) -> float | None:
    """CAGR from the first to the last value over *years* periods.

    ``values`` must be ordered newest-first (yfinance convention).
    Requires at least ``years + 1`` data points and both endpoints > 0.
    """
    if len(values) < years + 1:
        return None
    start = values[years]   # oldest available
    end   = values[0]       # most recent
    if start <= 0 or end <= 0:
        return None
    try:
        return round((end / start) ** (1.0 / years) - 1, 4)
    except (ZeroDivisionError, ValueError):
        return None


def compute_margin_series(numerator: list[float],
                          denominator: list[float],
                          max_years: int = 5) -> list[float | None]:
    """Return a margin series (numerator / denominator) newest-first.

    Length is min(len(numerator), len(denominator), max_years).
    """
    n = min(len(numerator), len(denominator), max_years)
    result: list[float | None] = []
    for i in range(n):
        if denominator[i] and denominator[i] != 0:
            result.append(round(numerator[i] / denominator[i] * 100, 2))
        else:
            result.append(None)
    return result


def margin_trend(series: list[float | None]) -> str:
    """Classify a margin series (newest-first) as EXPANDING / COMPRESSING /
    STABLE / INSUFFICIENT_DATA based on simple first-vs-last comparison."""
    clean = [v for v in series if v is not None]
    if len(clean) < 2:
        return "INSUFFICIENT_DATA"
    delta = clean[0] - clean[-1]   # newest minus oldest
    if delta > 1.0:
        return "EXPANDING"
    if delta < -1.0:
        return "COMPRESSING"
    return "STABLE"


def compute_roe_series(net_income: list[float],
                       equity: list[float],
                       max_years: int = 5) -> list[float | None]:
    """Return annual ROE % series, newest-first."""
    return compute_margin_series(net_income, equity, max_years)


def compute_fcf(op_cashflow: list[float],
                capex: list[float],
                max_years: int = 5) -> list[float | None]:
    """Derive Free Cash Flow = Operating CF − |Capex| (capex is negative
    in yfinance convention, so we always subtract the absolute value)."""
    n = min(len(op_cashflow), len(capex), max_years)
    result: list[float | None] = []
    for i in range(n):
        cap = capex[i]
        # yfinance returns capex as negative; normalise
        cap_abs = abs(cap) if cap is not None else 0.0
        result.append(round(op_cashflow[i] - cap_abs, 0))
    return result


def compute_fcf_conversion(fcf: list[float | None],
                           net_income: list[float],
                           max_years: int = 5) -> list[float | None]:
    """FCF conversion = FCF / Net Income × 100 (%)."""
    n = min(len(fcf), len(net_income), max_years)
    result: list[float | None] = []
    for i in range(n):
        f = fcf[i]
        ni = net_income[i]
        if f is not None and ni and ni != 0:
            result.append(round(f / ni * 100, 1))
        else:
            result.append(None)
    return result


def _template_key(sector: str | None, industry: str | None = None) -> str:
    """Map yfinance sector/industry string to a SECTOR_TEMPLATES key."""
    s = (sector or "").lower()
    i = (industry or "").lower()
    combined = s + " " + i
    if any(k in combined for k in ("bank", "financial", "insurance", "credit")):
        return "banking"
    if any(k in combined for k in ("technology", "software", "semiconductor",
                                    "information", "internet")):
        return "technology"
    if any(k in combined for k in ("pharma", "drug", "biotech", "healthcare",
                                    "medical", "life science")):
        return "pharma"
    if any(k in combined for k in ("energy", "oil", "gas", "petroleum")):
        return "energy"
    if any(k in combined for k in ("consumer cyclical", "retail",
                                    "apparel", "restaurant", "auto")):
        return "consumer_cyclical"
    return "default"


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------

def compute_financial_trends(
    income: Any,
    balance: Any,
    cashflow: Any,
    *,
    max_years: int = 5,
) -> dict:
    """Compute all trend metrics from raw yfinance DataFrames.

    All inputs may be None (treated as empty).  Returns a dict of
    computed metrics — always structurally complete, with None values
    where data is missing.

    Parameters
    ----------
    income, balance, cashflow
        yfinance annual statement DataFrames (columns = dates, rows = items).
    max_years
        Number of annual periods to consider (default 5).
    """
    # ── Revenue ──────────────────────────────────────────────────────────
    revenue = _row(income, "Total Revenue", "Revenue")
    gross_profit = _row(income, "Gross Profit")
    op_income = _row(income, "Operating Income", "EBIT",
                     "Total Operating Income As Reported")
    net_income = _row(income, "Net Income", "Net Income Common Stockholders",
                      "Net Income Including Noncontrolling Interests")
    rd_expense = _row(income, "Research And Development",
                      "Research & Development",
                      "Research Development Expenses")
    interest_expense = _row(income, "Interest Expense",
                            "Net Interest Income")   # banks use NII

    # ── Balance Sheet ────────────────────────────────────────────────────
    total_equity = _row(balance, "Common Stock Equity",
                        "Stockholders Equity",
                        "Total Equity Gross Minority Interest")
    long_term_debt = _row(balance, "Long Term Debt",
                          "Net Long Term Debt",
                          "Long Term Debt And Capital Lease Obligation")
    total_assets = _row(balance, "Total Assets")
    current_assets = _row(balance, "Current Assets")
    current_liab = _row(balance, "Current Liabilities")

    # ── Cash Flow ────────────────────────────────────────────────────────
    op_cf = _row(cashflow, "Operating Cash Flow",
                 "Cash Flow From Continuing Operating Activities")
    capex = _row(cashflow, "Capital Expenditure",
                 "Purchase Of PPE",
                 "Capital Expenditures Reported")
    free_cf_direct = _row(cashflow, "Free Cash Flow")

    # ── Derived metrics ──────────────────────────────────────────────────
    revenue_cagr_3y = compute_cagr(revenue, 3)
    revenue_cagr_5y = compute_cagr(revenue, 5)

    gross_margins = compute_margin_series(gross_profit, revenue, max_years)
    op_margins    = compute_margin_series(op_income, revenue, max_years)
    net_margins   = compute_margin_series(net_income, revenue, max_years)
    rd_intensities = compute_margin_series(rd_expense, revenue, max_years)
    roe_series    = compute_roe_series(net_income, total_equity, max_years)

    # FCF — prefer directly reported, derive if absent
    if free_cf_direct:
        fcf_series = [_safe_float(v) for v in free_cf_direct[:max_years]]
    elif op_cf and capex:
        fcf_series = compute_fcf(op_cf, capex, max_years)
    else:
        fcf_series = []

    fcf_conv = compute_fcf_conversion(fcf_series, net_income, max_years)

    # D/E ratio (latest year)
    debt_equity_latest: float | None = None
    if long_term_debt and total_equity and total_equity[0] and total_equity[0] != 0:
        debt_equity_latest = round(long_term_debt[0] / abs(total_equity[0]), 2)

    # Current ratio (latest)
    current_ratio_latest: float | None = None
    if current_assets and current_liab and current_liab[0] and current_liab[0] != 0:
        current_ratio_latest = round(current_assets[0] / current_liab[0], 2)

    # ── Summary scalars (latest year unless noted) ────────────────────────
    def _latest(series: list) -> float | None:
        clean = [v for v in series if v is not None]
        return clean[0] if clean else None

    return {
        # Revenue
        "revenue_cagr_3y":          revenue_cagr_3y,
        "revenue_cagr_5y":          revenue_cagr_5y,
        "revenue_series":           [_safe_float(v) for v in revenue[:max_years]],
        # Margins (latest + trend)
        "gross_margin_pct_latest":  _latest(gross_margins),
        "gross_margin_series":      gross_margins,
        "op_margin_pct_latest":     _latest(op_margins),
        "op_margin_series":         op_margins,
        "op_margin_trend":          margin_trend(op_margins),
        "net_margin_pct_latest":    _latest(net_margins),
        "net_margin_series":        net_margins,
        "net_margin_trend":         margin_trend(net_margins),
        # R&D
        "rd_intensity_pct_latest":  _latest(rd_intensities),
        "rd_intensity_series":      rd_intensities,
        # Profitability
        "roe_pct_latest":           _latest(roe_series),
        "roe_series":               roe_series,
        # Cash conversion
        "fcf_latest":               _latest(fcf_series),
        "fcf_series":               fcf_series,
        "fcf_conversion_latest":    _latest(fcf_conv),
        "fcf_conversion_series":    fcf_conv,
        # Balance sheet
        "debt_equity_latest":       debt_equity_latest,
        "current_ratio_latest":     current_ratio_latest,
        # Interest
        "interest_expense_latest":  _safe_float(interest_expense[0]) if interest_expense else None,
    }


def _quality_verdict(trends: dict, info: dict) -> dict:
    """Simple rules-based quality verdict from trend metrics + info dict.

    Returns a dict: grade (A/B/C/D/F), score (0-100), signals (list of
    positives / negatives as human-readable strings).
    """
    positives: list[str] = []
    negatives: list[str] = []
    score = 50  # start neutral

    # Revenue growth
    cagr3 = trends.get("revenue_cagr_3y")
    if cagr3 is not None:
        pct = cagr3 * 100
        if pct >= 15:
            positives.append(f"Strong revenue growth: {pct:.1f}% 3y CAGR")
            score += 10
        elif pct >= 8:
            positives.append(f"Healthy revenue growth: {pct:.1f}% 3y CAGR")
            score += 5
        elif pct < 0:
            negatives.append(f"Revenue shrinking: {pct:.1f}% 3y CAGR")
            score -= 10

    # Operating margin trend
    om_trend = trends.get("op_margin_trend")
    if om_trend == "EXPANDING":
        positives.append("Operating margin expanding")
        score += 8
    elif om_trend == "COMPRESSING":
        negatives.append("Operating margin compressing")
        score -= 8

    # ROE
    roe = trends.get("roe_pct_latest")
    if roe is not None:
        if roe >= 20:
            positives.append(f"High ROE: {roe:.1f}%")
            score += 10
        elif roe >= 12:
            positives.append(f"Acceptable ROE: {roe:.1f}%")
            score += 4
        elif roe < 5:
            negatives.append(f"Weak ROE: {roe:.1f}%")
            score -= 8

    # FCF conversion
    fcf_conv = trends.get("fcf_conversion_latest")
    if fcf_conv is not None:
        if fcf_conv >= 80:
            positives.append(f"Strong FCF conversion: {fcf_conv:.0f}%")
            score += 8
        elif fcf_conv >= 50:
            positives.append(f"Adequate FCF conversion: {fcf_conv:.0f}%")
            score += 3
        elif fcf_conv < 20:
            negatives.append(f"Weak FCF conversion: {fcf_conv:.0f}%")
            score -= 8

    # Debt
    de = trends.get("debt_equity_latest")
    if de is not None:
        if de < 0.5:
            positives.append(f"Low leverage: D/E {de:.2f}")
            score += 5
        elif de > 2.0:
            negatives.append(f"High leverage: D/E {de:.2f}")
            score -= 8

    # Valuation (from info)
    fwd_pe = _safe_float(info.get("forwardPE"))
    if fwd_pe is not None and 5 < fwd_pe < 80:
        if fwd_pe < 15:
            positives.append(f"Attractive forward P/E: {fwd_pe:.1f}x")
            score += 5
        elif fwd_pe > 40:
            negatives.append(f"Rich valuation: forward P/E {fwd_pe:.1f}x")
            score -= 5

    score = max(0, min(100, score))
    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 50:
        grade = "C"
    elif score >= 35:
        grade = "D"
    else:
        grade = "F"

    return {
        "grade": grade,
        "score": score,
        "positives": positives,
        "negatives": negatives,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyse_long_term(
    symbol: str,
    years: int = 5,
    *,
    include_peers: bool = True,
    _ticker_factory: Any = None,   # injection seam for tests
) -> dict:
    """Full long-term fundamental analysis for *symbol*.

    Fetches annual financials (income stmt / balance sheet / cash flow)
    via yfinance, computes multi-year trend metrics, classifies sector,
    applies a quality verdict, and optionally runs peer comparison.

    Parameters
    ----------
    symbol
        Ticker symbol (e.g. "AAPL", "HDFCBANK.NS", "TCS.NS", "HSBA.L").
    years
        Number of annual periods to analyse (default 5; yfinance typically
        provides up to 4 years of annual data for most stocks).
    include_peers
        If True and peers are known, run the same analysis on up to 3 peers
        for side-by-side context (adds ~2-4s per peer).
    _ticker_factory
        Optional callable ``sym → Ticker``.  Pass a mock in tests to avoid
        any network calls.

    Returns
    -------
    dict with keys:
        symbol, fetched_at, sector, industry, template_key,
        template (sector metadata + gaps + analyst_note),
        trends (computed metrics), quality (grade + signals),
        info_snapshot (valuation multiples from Ticker.info),
        peers (list of peer result dicts, abbreviated),
        warnings (list of data-quality warnings),
        _source (cite string).
    """
    sym = symbol.upper()
    fetched_at = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []

    # ── Fetch ─────────────────────────────────────────────────────────────
    try:
        if _ticker_factory is not None:
            ticker = _ticker_factory(sym)
        else:
            import yfinance as yf  # noqa: PLC0415
            ticker = yf.Ticker(sym)

        info = {}
        try:
            info = ticker.info or {}
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"info fetch failed: {exc}")

        income, balance, cashflow = None, None, None
        try:
            income = ticker.income_stmt
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"income_stmt fetch failed: {exc}")
        try:
            balance = ticker.balance_sheet
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"balance_sheet fetch failed: {exc}")
        try:
            cashflow = ticker.cashflow
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"cashflow fetch failed: {exc}")

    except Exception as exc:  # noqa: BLE001
        return {
            "_source": f"error://fundamental_analysis/{sym}",
            "fetched_at": fetched_at,
            "ok": False,
            "error": str(exc),
            "symbol": sym,
        }

    # ── Sector template ───────────────────────────────────────────────────
    sector   = info.get("sector") or ""
    industry = info.get("industry") or ""
    tpl_key  = _template_key(sector, industry)
    template = SECTOR_TEMPLATES[tpl_key]

    # Warn on ADR FX drag
    exchange = info.get("exchange") or ""
    if sym.endswith((".NS", ".BO")):
        pass  # NSE / BSE — INR denominated, clean
    elif exchange in ("NYQ", "NMS", "NGM") and any(
        kw in industry.lower() for kw in ("bank", "financial", "technology")
    ):
        # Could be an ADR; check
        if info.get("currency", "USD") == "USD" and any(
            sym.endswith(suffix) for suffix in ("B", "N", "Y")
        ):
            warnings.append(
                f"{sym} may be a USD ADR — price includes FX drag.  "
                "For INR fundamentals consider the .NS ticker instead."
            )

    if sector == "" or sector is None:
        warnings.append("sector not available from yfinance — sector template defaulted")

    # ── Compute trends ────────────────────────────────────────────────────
    trends = compute_financial_trends(income, balance, cashflow, max_years=years)

    # Check for sparse data
    if not trends["revenue_series"]:
        warnings.append("No revenue data returned — yfinance may not cover this ticker")
    elif len(trends["revenue_series"]) < 3:
        warnings.append(
            f"Only {len(trends['revenue_series'])} year(s) of revenue data "
            "— CAGR calculations may be unreliable"
        )

    # ── Info snapshot (valuation multiples) ───────────────────────────────
    info_snapshot = {
        "market_cap":        info.get("marketCap"),
        "currency":          info.get("currency"),
        "forward_pe":        _safe_float(info.get("forwardPE")),
        "trailing_pe":       _safe_float(info.get("trailingPE")),
        "price_to_book":     _safe_float(info.get("priceToBook")),
        "price_to_sales":    _safe_float(info.get("priceToSalesTrailing12Months")),
        "ev_to_ebitda":      _safe_float(info.get("enterpriseToEbitda")),
        "roe_ttm_pct":       (
            round(_safe_float(info.get("returnOnEquity")) * 100, 2)
            if _safe_float(info.get("returnOnEquity")) is not None else None
        ),
        "profit_margin_pct": (
            round(_safe_float(info.get("profitMargins")) * 100, 2)
            if _safe_float(info.get("profitMargins")) is not None else None
        ),
        "dividend_yield_pct":(
            round(_safe_float(info.get("dividendYield")) * 100, 2)
            if _safe_float(info.get("dividendYield")) is not None else None
        ),
        "beta":              _safe_float(info.get("beta")),
        "52w_high":          _safe_float(info.get("fiftyTwoWeekHigh")),
        "52w_low":           _safe_float(info.get("fiftyTwoWeekLow")),
        "analyst_target":    _safe_float(info.get("targetMeanPrice")),
    }

    # ── Quality verdict ───────────────────────────────────────────────────
    quality = _quality_verdict(trends, info)

    # ── Peers ─────────────────────────────────────────────────────────────
    peer_results: list[dict] = []
    if include_peers:
        peer_syms = KNOWN_PEERS.get(sym, [])[:3]
        for psym in peer_syms:
            try:
                pr = analyse_long_term(
                    psym, years=years,
                    include_peers=False,
                    _ticker_factory=_ticker_factory,
                )
                # Abbreviate peer result to key metrics only
                peer_results.append({
                    "symbol": psym,
                    "grade": pr.get("quality", {}).get("grade"),
                    "revenue_cagr_3y":       pr.get("trends", {}).get("revenue_cagr_3y"),
                    "op_margin_pct_latest":  pr.get("trends", {}).get("op_margin_pct_latest"),
                    "roe_pct_latest":        pr.get("trends", {}).get("roe_pct_latest"),
                    "fcf_conversion_latest": pr.get("trends", {}).get("fcf_conversion_latest"),
                    "forward_pe":            pr.get("info_snapshot", {}).get("forward_pe"),
                    "debt_equity_latest":    pr.get("trends", {}).get("debt_equity_latest"),
                    "error":                 pr.get("error"),
                })
            except Exception as exc:  # noqa: BLE001
                peer_results.append({"symbol": psym, "error": str(exc)})

    # ── Assemble result ───────────────────────────────────────────────────
    return {
        "_source":      f"live://fundamentals/longterm/{sym}",
        "fetched_at":   fetched_at,
        "ok":           True,
        "symbol":       sym,
        "sector":       sector or None,
        "industry":     industry or None,
        "template_key": tpl_key,
        "template":     template,
        "trends":       trends,
        "quality":      quality,
        "info_snapshot": info_snapshot,
        "peers":        peer_results,
        "warnings":     warnings,
    }


__all__ = [
    "analyse_long_term",
    "compute_financial_trends",
    "compute_cagr",
    "compute_margin_series",
    "margin_trend",
    "compute_fcf",
    "compute_fcf_conversion",
    "SECTOR_TEMPLATES",
    "KNOWN_PEERS",
]
