"""Gem Hunter v2 — TRADEPRO Phase G hardened rules.

Existing comparator favours uptrends. Gem hunter is the contrarian
lens: quality names beaten down to a real entry. v2 reflects the
2026-05-09 design review which sharpened every gate after the user
made the case that contrarian-with-loose-rules is where retail
investors lose serious money.

KEY V2 CHANGES VS V1
====================
1. **Single-stock vs ETF differentiation.** Stocks must clear a
   higher bar — Sharpe ≥ 0.7 (vs 0.5 for ETFs), ≥2 recovery
   signals (vs 1 for ETFs), AND a fundamentals quality floor
   (debt/equity < 1.5 + free cash flow > 0). The cost of being
   wrong on a single stock (it can go to zero — Wirecard, ASOS)
   is fundamentally different from a 100-700 holding ETF.
2. **Sentiment guard tightened.** Mean ≥ -0.15 (was -0.30) AND
   zero very-negative headlines AND ≤1 material-negative. Mean-
   sentiment alone hides the tail risk where one catastrophic
   headline (fraud / regulatory action) sits inside otherwise
   neutral coverage.
3. **Forced ≥HIGH risk rating.** Every gem is auto-bumped to at
   least HIGH regardless of vol baseline; if the volatility-rule
   said LOW, the audit trail flags the deliberate override
   ("rated LOW by volatility, forced HIGH due to contrarian
   status"). Volatility doesn't capture fundamental contrarian risk.
4. **Halved + tiered position caps.** LOW gem 12% / MED 8% /
   HIGH 5% / EXTREME 2%. Compressed at the bottom because that's
   where ruin risk lives.
5. **Settled-dust proxy.** Until persistence ships, require
   `momentum_3m_pct ≥ -8%` — a name that's still falling rapidly
   over 3 months hasn't bottomed yet.
6. **Sector concentration banner, not cap.** Surface "5 of 7
   gems are in XLE — sector rotation signal" rather than capping
   the list. Output flag the consumer renders.

EXIT FRAMEWORK (NEW IN V2)
==========================
A gem hunter without exit discipline is half a system. We ship
two of three exit triggers now (the third needs entry-price
persistence which lands with Phase D):

  Trigger 1 — Reclassification: RSI > 65 AND above SMA200 AND
    recovered ≥60% of original drawdown → no longer a gem.
    Forces "take profit / hold under different criteria" decision.
  Trigger 3 — Thesis broken: sentiment drops below -0.30 OR
    debt/equity spikes >1.5 OR FCF turns negative OR cross-basket
    z turns negative. Auto-flag for sell consideration.
  Trigger 2 — Profit ladder (DEFERRED): 25%/50%/trailing-stop
    requires entry-price persistence. Phase D unblocks this.

PHASE 2 GATES (DEFERRED)
========================
  - Valuation gate: forward P/E ≤ 80% of own 5y avg AND below
    sector median. Needs the historical-P/E snapshot store.
  - Catalyst gate: insider buying / analyst upgrade post-fall /
    earnings beat with stock fall. Needs additional yfinance
    pulls (insider_purchases, recommendations).
  - Earnings trajectory gate: forward EPS estimate flat or
    rising over 90d. Needs persistence.
  Phase D unblocks all three.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Tighter v2 thresholds. Constants at the top so a reader can argue
# with any number and we change it in one place.
QUALITY_MIN_SHARPE_ETF = 0.5
QUALITY_MIN_SHARPE_STOCK = 0.7
QUALITY_MAX_RECOVERY_DAYS = 365 * 2          # 24mo
DEEP_DRAWDOWN_PCT = -25.0
RANGE_LOW_PCTILE = 25.0
SENTIMENT_FLOOR = -0.15                       # was -0.30 in v1
MAX_VERY_NEGATIVE_HEADLINES = 0
MAX_MATERIAL_NEGATIVE_HEADLINES = 1
RSI_OVERSOLD_RECOVERY_MIN = 35.0
RSI_OVERSOLD_RECOVERY_MAX = 50.0
SETTLED_DUST_MOMENTUM_3M_FLOOR = -8.0         # 3m return ≥ -8% — dust settled
STOCK_DEBT_TO_EQUITY_CEILING = 1.5            # heavy leverage filter
STOCK_FCF_FLOOR = 0.0                         # must generate cash
STOCK_RECOVERY_SIGNALS_REQUIRED = 2
ETF_RECOVERY_SIGNALS_REQUIRED = 1
N_HOLDINGS_ETF_THRESHOLD = 30                 # ≥30 holdings → ETF profile

# Halved + tiered position caps (v2). Compressed at the bottom
# because EXTREME-gem ruin risk needs aggressive sizing discipline.
_GEM_POSITION_CAPS = {
    "LOW": 12.0,
    "MEDIUM": 8.0,
    "HIGH": 5.0,
    "EXTREME": 2.0,
}

# Exit-trigger thresholds.
RECLASSIFY_RSI_CEILING = 65.0
RECLASSIFY_DD_RECOVERED_PCT = 60.0
THESIS_BROKEN_SENTIMENT_FLOOR = -0.30
THESIS_BROKEN_DTE_CEILING = 1.5


@dataclass
class GemReason:
    quality: list[str] = field(default_factory=list)
    drawdown: list[str] = field(default_factory=list)
    range_position: list[str] = field(default_factory=list)
    valuation: list[str] = field(default_factory=list)
    fundamentals_quality: list[str] = field(default_factory=list)
    settled_dust: list[str] = field(default_factory=list)
    recovery_signals: list[str] = field(default_factory=list)
    risk_override: str | None = None  # set when we force ≥HIGH
    failed_filters: list[str] = field(default_factory=list)

    def all_passing(self) -> list[str]:
        bag = [
            *self.quality,
            *self.drawdown,
            *self.range_position,
            *self.valuation,
            *self.fundamentals_quality,
            *self.settled_dust,
            *self.recovery_signals,
        ]
        if self.risk_override:
            bag.append(self.risk_override)
        return bag


@dataclass
class GemVerdict:
    is_gem: bool
    symbol: str
    profile: str                    # "etf" or "stock"
    score: int
    forced_risk: str | None         # auto-bumped risk rating ("HIGH" or "EXTREME")
    position_cap_pct: float         # advisory cap given the forced rating
    reasons: GemReason

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_gem": self.is_gem,
            "symbol": self.symbol,
            "profile": self.profile,
            "score": self.score,
            "forced_risk": self.forced_risk,
            "position_cap_pct": self.position_cap_pct,
            "reasons": {
                "passing": self.reasons.all_passing(),
                "failed_filters": list(self.reasons.failed_filters),
                "recovery_signals": list(self.reasons.recovery_signals),
            },
        }


@dataclass
class GemExitVerdict:
    """Exit framework — fires when a position the user holds (or could
    hold) as a gem has either reclassified out or had its thesis broken.

    Trigger 2 (profit ladder) requires knowing the entry price; that
    lands with Phase D portfolio persistence. v2 ships triggers 1 + 3
    which can be evaluated from today's row alone."""
    action: str                  # "RECLASSIFIED" / "THESIS_BROKEN" / "HOLD"
    triggered: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "triggered": self.triggered,
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return v


def _is_etf(row: dict) -> bool:
    """Stocks vs ETFs need different bars per the v2 design review.
    yfinance is consistent enough on legal_type / quoteType to use as
    primary signal; n_holdings ≥30 fallback catches mis-labelled
    multi-asset funds."""
    fund = row.get("fundamentals") or {}
    legal = (fund.get("legal_type") or "").upper()
    if "ETF" in legal or legal == "EXCHANGE TRADED FUND":
        return True
    n_holdings = fund.get("n_holdings")
    if isinstance(n_holdings, (int, float)) and n_holdings >= N_HOLDINGS_ETF_THRESHOLD:
        return True
    if legal in {"EQUITY", "COMMON STOCK"}:
        return False
    # Default to "stock" — safer to apply the stricter bar when type is
    # unknown than mistakenly treat a single stock as an ETF.
    return False


# ---------------------------------------------------------------------------
# Entry evaluation
# ---------------------------------------------------------------------------


def evaluate_gem(row: dict) -> GemVerdict:
    """Score one row against the v2 gem profile."""
    sym = row.get("symbol") or "?"
    is_etf = _is_etf(row)
    profile = "etf" if is_etf else "stock"

    ms = row.get("market_state") or {}
    stats = row.get("stats") or {}
    sentiment = row.get("sentiment_summary") or {}
    fund = row.get("fundamentals") or {}
    val = row.get("valuation_flag") or {}
    cs = row.get("cross_sectional_momentum") or {}
    rr = row.get("risk_rating") or {}

    reasons = GemReason()
    required_pass = True

    # ---- Quality intact ----
    sharpe = _f(stats.get("sharpe"))
    sharpe_floor = QUALITY_MIN_SHARPE_ETF if is_etf else QUALITY_MIN_SHARPE_STOCK
    if sharpe is not None and sharpe >= sharpe_floor:
        reasons.quality.append(
            f"Sharpe {sharpe:.2f} ≥ {sharpe_floor} ({profile} bar)"
        )
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"Sharpe {sharpe if sharpe is not None else 'unknown'} below {profile} floor {sharpe_floor}"
        )

    rec_days = _f(stats.get("max_drawdown_recovery_days"))
    still_recovering = bool(stats.get("max_drawdown_still_recovering"))
    if not still_recovering and rec_days is not None and rec_days <= QUALITY_MAX_RECOVERY_DAYS:
        reasons.quality.append(
            f"recovers from drawdowns in {int(rec_days / 30)}mo (≤ 24mo)"
        )
    else:
        required_pass = False
        if still_recovering:
            reasons.failed_filters.append("still recovering from worst DD — value-trap risk")
        else:
            reasons.failed_filters.append(
                f"slow recoverer ({int((rec_days or 0)/30)}mo > 24mo)"
            )

    # ---- Real correction ----
    dd = _f(ms.get("drawdown_from_peak_pct"))
    if dd is not None and dd <= DEEP_DRAWDOWN_PCT:
        reasons.drawdown.append(f"{dd:.1f}% from 5y peak (≤ {DEEP_DRAWDOWN_PCT}%)")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"only {dd or 0:.1f}% from 5y peak — not a real correction"
        )

    # ---- Bottom of the range ----
    rp = _f(ms.get("range_position_pct"))
    if rp is not None and rp <= RANGE_LOW_PCTILE:
        reasons.range_position.append(f"{rp:.0f}th pctile of 52w range (≤ {RANGE_LOW_PCTILE:.0f}th)")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"{rp or 0:.0f}th pctile of 52w — not near the floor"
        )

    # ---- Valuation lens (CHEAP per basket-relative) ----
    flag = (val.get("flag") or "").lower()
    if flag == "cheap":
        reasons.valuation.append(val.get("basis") or "cheap vs basket peers")
    else:
        required_pass = False
        reasons.failed_filters.append(
            f"valuation flag '{flag or 'n/a'}' — not in the cheap quartile"
        )

    # ---- Stock-only fundamentals quality floor ----
    if not is_etf:
        dte = _f(fund.get("debt_to_equity"))
        fcf = _f(fund.get("free_cashflow"))
        # debt/equity is a guard, not a hard requirement when missing —
        # emerging-growth names sometimes lack a clean reading. Penalise
        # only when we have data AND it breaches.
        if dte is not None and dte > STOCK_DEBT_TO_EQUITY_CEILING:
            required_pass = False
            reasons.failed_filters.append(
                f"debt/equity {dte:.2f} > {STOCK_DEBT_TO_EQUITY_CEILING} — over-leveraged"
            )
        elif dte is not None:
            reasons.fundamentals_quality.append(f"debt/equity {dte:.2f} (≤ 1.5)")
        if fcf is not None and fcf <= STOCK_FCF_FLOOR:
            required_pass = False
            reasons.failed_filters.append(
                f"free cash flow {fcf:,.0f} ≤ {STOCK_FCF_FLOOR} — not generating cash"
            )
        elif fcf is not None:
            # Show in millions / billions for readability.
            if abs(fcf) > 1e9:
                fcf_str = f"FCF {fcf/1e9:+.1f}B"
            elif abs(fcf) > 1e6:
                fcf_str = f"FCF {fcf/1e6:+.0f}M"
            else:
                fcf_str = f"FCF {fcf:+,.0f}"
            reasons.fundamentals_quality.append(fcf_str + " > 0")

    # ---- Sentiment v2 (mean + tail-risk guards) ----
    mean_sent = _f(sentiment.get("mean_sentiment"))
    very_neg = sentiment.get("very_negative_count")
    mat_neg = sentiment.get("material_negative_count")
    sent_ok = True
    if mean_sent is not None and mean_sent < SENTIMENT_FLOOR:
        sent_ok = False
        required_pass = False
        reasons.failed_filters.append(
            f"sentiment mean {mean_sent:+.2f} < floor {SENTIMENT_FLOOR}"
        )
    if isinstance(very_neg, (int, float)) and very_neg > MAX_VERY_NEGATIVE_HEADLINES:
        sent_ok = False
        required_pass = False
        reasons.failed_filters.append(
            f"{int(very_neg)} very-negative headlines (≤ {MAX_VERY_NEGATIVE_HEADLINES} allowed) — tail risk"
        )
    if isinstance(mat_neg, (int, float)) and mat_neg > MAX_MATERIAL_NEGATIVE_HEADLINES:
        sent_ok = False
        required_pass = False
        reasons.failed_filters.append(
            f"{int(mat_neg)} material-negative headlines (≤ {MAX_MATERIAL_NEGATIVE_HEADLINES} allowed)"
        )

    # ---- Settled-dust proxy ----
    mom_3m = _f(ms.get("momentum_3m_pct"))
    if mom_3m is not None and mom_3m >= SETTLED_DUST_MOMENTUM_3M_FLOOR:
        reasons.settled_dust.append(
            f"3m return {mom_3m:+.1f}% ≥ {SETTLED_DUST_MOMENTUM_3M_FLOOR}% — dust settling"
        )
    elif mom_3m is not None:
        required_pass = False
        reasons.failed_filters.append(
            f"3m return {mom_3m:+.1f}% < {SETTLED_DUST_MOMENTUM_3M_FLOOR}% — still falling fast"
        )
    # If we have no 3m data, don't fail — the other checks are enough
    # to filter; settled-dust is a tightener, not the primary gate.

    # ---- Recovery signals ----
    rsi = _f(ms.get("rsi_14"))
    above_sma = ms.get("above_sma_200")
    z = _f(cs.get("zscore"))

    if (rsi is not None
            and RSI_OVERSOLD_RECOVERY_MIN <= rsi <= RSI_OVERSOLD_RECOVERY_MAX):
        reasons.recovery_signals.append(
            f"RSI {rsi:.0f} bouncing out of oversold ({RSI_OVERSOLD_RECOVERY_MIN:.0f}-{RSI_OVERSOLD_RECOVERY_MAX:.0f})"
        )
    if above_sma is True:
        reasons.recovery_signals.append("price above SMA200 — trend potentially turning")
    if z is not None and z > 0:
        reasons.recovery_signals.append(
            f"cross-basket z {z:+.2f} — outperforming peers from a low base"
        )

    required_signals = (
        ETF_RECOVERY_SIGNALS_REQUIRED if is_etf
        else STOCK_RECOVERY_SIGNALS_REQUIRED
    )
    if len(reasons.recovery_signals) < required_signals:
        required_pass = False
        reasons.failed_filters.append(
            f"only {len(reasons.recovery_signals)} of {required_signals} required recovery signals"
        )

    # ---- Forced ≥HIGH risk + mismatch annotation ----
    actual_rating = (rr.get("rating") or "").upper()
    forced_risk: str | None = None
    if required_pass:
        if actual_rating == "EXTREME":
            forced_risk = "EXTREME"
        else:
            forced_risk = "HIGH"
        if actual_rating and actual_rating not in ("HIGH", "EXTREME"):
            reasons.risk_override = (
                f"rated {actual_rating} by volatility, forced HIGH due to "
                f"contrarian status (gems carry inherent fundamental risk "
                f"the vol model doesn't capture)"
            )

    score = (
        len(reasons.quality)
        + len(reasons.drawdown)
        + len(reasons.range_position)
        + len(reasons.valuation)
        + len(reasons.fundamentals_quality)
        + len(reasons.settled_dust)
        + min(2, len(reasons.recovery_signals))
    )
    pos_cap = (
        _GEM_POSITION_CAPS.get(forced_risk or "HIGH", 5.0)
        if required_pass else 0.0
    )

    return GemVerdict(
        is_gem=required_pass,
        symbol=sym,
        profile=profile,
        score=score,
        forced_risk=forced_risk,
        position_cap_pct=pos_cap,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Exit framework
# ---------------------------------------------------------------------------


def evaluate_gem_exit(row: dict) -> GemExitVerdict:
    """Triggers 1 + 3 from the v2 exit framework. Trigger 2 (profit
    ladder) needs entry-price persistence and ships with Phase D.

    Trigger 1 — RECLASSIFIED: the gem worked. RSI has moved healthy,
    price reclaimed SMA200, and we've recovered ≥60% of the original
    drawdown. Forces a re-evaluation under standard criteria.

    Trigger 3 — THESIS_BROKEN: a gate that originally qualified the
    gem has flipped. Sentiment fell hostile, debt/equity blew out,
    FCF turned negative, or the symbol is now underperforming peers.
    Auto-flag for sell.
    """
    ms = row.get("market_state") or {}
    sentiment = row.get("sentiment_summary") or {}
    fund = row.get("fundamentals") or {}
    cs = row.get("cross_sectional_momentum") or {}

    rsi = _f(ms.get("rsi_14"))
    above_sma = ms.get("above_sma_200")
    dd = _f(ms.get("drawdown_from_peak_pct"))
    mean_sent = _f(sentiment.get("mean_sentiment"))
    dte = _f(fund.get("debt_to_equity"))
    fcf = _f(fund.get("free_cashflow"))
    z = _f(cs.get("zscore"))

    # ---- Trigger 1: reclassified ----
    # We don't track entry-DD without persistence; use "current DD
    # ≥ -10%" as the proxy for "recovered ≥60% of -25% to -40% range".
    # When the persistence layer ships we'll replace this with the
    # actual recovery percentage from the entry mark.
    reclassified = (
        rsi is not None and rsi > RECLASSIFY_RSI_CEILING
        and above_sma is True
        and dd is not None and dd >= -10.0
    )
    if reclassified:
        return GemExitVerdict(
            action="RECLASSIFIED",
            triggered=True,
            reasons=[
                f"RSI {rsi:.0f} > {RECLASSIFY_RSI_CEILING:.0f} (no longer oversold)",
                "price above SMA200",
                f"drawdown {dd:.1f}% — substantial recovery",
                "→ no longer fits the gem profile; re-evaluate under standard criteria or take profit",
            ],
        )

    # ---- Trigger 3: thesis broken ----
    broken: list[str] = []
    if mean_sent is not None and mean_sent < THESIS_BROKEN_SENTIMENT_FLOOR:
        broken.append(
            f"sentiment {mean_sent:+.2f} below thesis floor {THESIS_BROKEN_SENTIMENT_FLOOR}"
        )
    if dte is not None and dte > THESIS_BROKEN_DTE_CEILING:
        broken.append(
            f"debt/equity {dte:.2f} above thesis ceiling {THESIS_BROKEN_DTE_CEILING}"
        )
    if fcf is not None and fcf < 0:
        broken.append(f"free cash flow turned negative ({fcf:,.0f})")
    if z is not None and z < 0:
        broken.append(
            f"cross-basket z {z:+.2f} — underperforming peers"
        )
    if broken:
        return GemExitVerdict(
            action="THESIS_BROKEN",
            triggered=True,
            reasons=broken + ["→ original entry case has degraded; flag for sell"],
        )

    return GemExitVerdict(action="HOLD", triggered=False)


# ---------------------------------------------------------------------------
# Sector concentration banner
# ---------------------------------------------------------------------------


def sector_concentration_banner(rows: list[dict], threshold_pct: float = 50.0) -> str | None:
    """When ≥threshold% of today's gems are in one sector, surface as a
    banner — that's a sector-rotation signal, more useful than the
    individual gem flags. Returns None when no concentration exists."""
    if not rows:
        return None
    sectors: dict[str, int] = {}
    for r in rows:
        # Yahoo's `sector` field on Ticker.info doesn't always make it
        # to our fundamentals object; try common keys, fall back to
        # the universe label as a coarse proxy (etf_us_sector → energy).
        fund = r.get("fundamentals") or {}
        sect = fund.get("sector") or fund.get("category") or r.get("universe") or "—"
        sectors[sect] = sectors.get(sect, 0) + 1
    total = sum(sectors.values())
    top_sector, top_count = max(sectors.items(), key=lambda kv: kv[1])
    pct = top_count / total * 100.0
    if pct >= threshold_pct and top_sector != "—":
        return (
            f"⚠ {top_count} of {total} gems are in {top_sector} "
            f"({pct:.0f}%). Likely sector rotation rather than stock-"
            f"specific opportunities — consider sector ETF for cleaner exposure."
        )
    return None


# ---------------------------------------------------------------------------
# Top-level scan
# ---------------------------------------------------------------------------


def find_gems(rows: list[dict]) -> list[dict]:
    """Run evaluate_gem across rows; return qualifiers sorted by score
    desc, then deepest drawdown first."""
    gems: list[tuple[GemVerdict, dict]] = []
    for r in rows:
        v = evaluate_gem(r)
        if v.is_gem:
            gems.append((v, r))
    gems.sort(
        key=lambda pair: (
            -pair[0].score,
            (pair[1].get("market_state") or {}).get("drawdown_from_peak_pct") or 0,
        ),
    )
    return [
        {**r, "gem_verdict": v.to_dict()}
        for v, r in gems
    ]
