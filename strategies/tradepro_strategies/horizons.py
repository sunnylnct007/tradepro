"""Investment Horizon Classification Engine.

Implements TRADEPRO-SPEC-001 §6.1 — splits a single instrument into
three independent horizon verdicts (swing / long-term / passive),
each with its own 0-8 score, signal grade and reasons.

The same instrument at the same price can simultaneously be:
  - a poor swing entry (no catalyst, near 52w highs)
  - a good long-term hold (quality fundamentals, dividend)
  - an excellent passive vehicle (low cost, broadly diversified)

Without horizon context the user can't distinguish these and may
misapply capital. The engine produces all three so the dashboard
and email digest can show them side-by-side.

Inputs come straight off the compare row — no new fetches:
  - market_state (RSI, SMA, 52w high/low/range_pct, momentum)
  - stats (Sharpe, CAGR)
  - swing_score (event layer → has_catalyst)
  - fundamentals (expense_ratio, n_holdings, dividend_yield, P/E)
  - external_consensus (analyst target_mean)
  - valuation_flag (CHEAP / FAIR / EXPENSIVE)

Open spec questions deliberately resolved (see TRADEPRO-SPEC-001
§10) until the snapshot store lands:
  Q1 — P/E vs 5y avg: use the basket-relative `valuation_flag`
       already shipped this session as a stand-in. CHEAP → 2pts
       (top-quartile cheap), FAIR → 1pt, EXPENSIVE → 0pts.
  Q3 — passive on individual stocks: `n_holdings == 1` returns
       a `signal: "N/A"` envelope on the passive horizon. The
       dashboard hides the pill; the email shows "Passive: N/A
       (single-stock — see Long-term)".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Single source of truth for the score → signal grade mapping.
# Spec §4.1 / §4.2 / §4.3 all use the same bands.
_BAND_BUY = 6
_BAND_WATCH = 4


def _grade(score: int) -> str:
    if score >= _BAND_BUY:
        return "BUY"
    if score >= _BAND_WATCH:
        return "WATCH"
    return "AVOID"


@dataclass
class HorizonVerdict:
    signal: str        # "BUY" | "WATCH" | "AVOID" | "N/A"
    score: str         # e.g. "5/8"  ("N/A" when not applicable)
    horizon: str       # human-readable window
    reasons: list[str] = field(default_factory=list)
    entry_note: str | None = None
    raw_score: int | None = None  # int form for the rationale layer

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "signal": self.signal,
            "score": self.score,
            "horizon": self.horizon,
            "reasons": list(self.reasons),
        }
        if self.entry_note is not None:
            out["entry_note"] = self.entry_note
        if self.raw_score is not None:
            out["raw_score"] = self.raw_score
        return out


@dataclass
class HorizonClassification:
    swing: HorizonVerdict
    long_term: HorizonVerdict
    passive: HorizonVerdict
    range_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "swing": self.swing.to_dict(),
            "long_term": self.long_term.to_dict(),
            "passive": self.passive.to_dict(),
            "range_pct": self.range_pct,
        }


def _f(x: Any, default: float = 0.0) -> float:
    """Coerce to float with a default; None / NaN / non-numeric → default."""
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return v


def _i(x: Any, default: int = 0) -> int:
    if x is None:
        return default
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def classify_horizons(symbol_data: dict) -> HorizonClassification:
    """Score one symbol across the three horizons.

    `symbol_data` is the per-row dict the comparator emits — accepts
    the live shape from `compare_run` without translation. Missing
    fields degrade gracefully: a symbol with no fundamentals can
    still get a swing score from market_state alone.
    """
    ms = symbol_data.get("market_state") or {}
    stats = symbol_data.get("stats") or {}
    sw = symbol_data.get("swing_score") or {}
    fund = symbol_data.get("fundamentals") or {}
    ext = symbol_data.get("external_consensus") or {}
    val = symbol_data.get("valuation_flag") or {}

    rsi = _f(ms.get("rsi_14"), 50.0)
    off_52w = abs(_f(ms.get("pct_off_52w_high_pct"), 0.0))
    above_sma = bool(ms.get("above_sma_200"))
    sharpe = _f(stats.get("sharpe"), 0.0)
    cagr = _f(stats.get("cagr_pct"), 0.0)
    momentum_3m = _f(ms.get("momentum_3m_pct"), 0.0)
    has_catalyst = _i((sw.get("layers") or {}).get("event"), 0) > 0
    pe_flag = (val.get("flag") or "FAIR").upper()
    n_holdings = fund.get("n_holdings")
    expense_ratio = _f(fund.get("expense_ratio_pct"), 99.0)
    div_yield = _f(fund.get("dividend_yield_pct"), 0.0)
    price = _f(ms.get("last_price"), 0.0)
    target = _f(ext.get("target_mean"), 0.0)
    analyst_upside = (
        ((target - price) / price * 100.0) if (target > 0 and price > 0) else 0.0
    )
    range_pct = symbol_data.get("range_pct")
    if range_pct is None:
        range_pct = ms.get("range_pct") or ms.get("range_position_pct")
    range_pct_f: float | None = (
        float(range_pct) if isinstance(range_pct, (int, float)) else None
    )

    swing = _score_swing(
        rsi=rsi, off_52w=off_52w, has_catalyst=has_catalyst,
        analyst_upside=analyst_upside, above_sma=above_sma,
        range_pct=range_pct_f,
    )
    long_term = _score_long_term(
        sharpe=sharpe, pe_flag=pe_flag, analyst_upside=analyst_upside,
        cagr=cagr, momentum_3m=momentum_3m,
    )
    passive = _score_passive(
        expense_ratio=expense_ratio, n_holdings=n_holdings,
        sharpe=sharpe, cagr=cagr, div_yield=div_yield,
        legal_type=(fund.get("legal_type") or "").upper(),
    )
    return HorizonClassification(
        swing=swing, long_term=long_term, passive=passive,
        range_pct=range_pct_f,
    )


def _score_swing(
    *, rsi: float, off_52w: float, has_catalyst: bool,
    analyst_upside: float, above_sma: bool, range_pct: float | None,
) -> HorizonVerdict:
    """Spec §4.1. Five base criteria up to 8 points, with a range-
    position modifier applied at the end:
      0-35 pctile (near lows)   → +1 bonus
      35-65 (mid)               → no modifier
      65-80 (near highs)        → -1 penalty
      80-100 (at highs)         → -2 penalty, capped at WATCH
    """
    score = 0
    reasons: list[str] = []

    # 1 — RSI (oversold = good swing setup)
    if rsi < 40:
        score += 2
        reasons.append(f"RSI {rsi:.0f} — oversold")
    elif rsi < 50:
        score += 1
        reasons.append(f"RSI {rsi:.0f} — cooling")

    # 2 — Distance from 52w high (room to recover)
    if off_52w > 10:
        score += 2
        reasons.append(f"{off_52w:.1f}% off 52w high")
    elif off_52w > 5:
        score += 1
        reasons.append(f"{off_52w:.1f}% off 52w high")

    # 3 — Active event catalyst inside the window
    if has_catalyst:
        score += 2
        reasons.append("Active event catalyst")

    # 4 — Sufficient analyst upside
    if analyst_upside > 12:
        score += 1
        reasons.append(f"{analyst_upside:.0f}% analyst upside")

    # 5 — Above 200-day SMA (don't catch a falling knife)
    if above_sma:
        score += 1

    # Range-position modifier (spec §5.2). The hard cap at WATCH for
    # near-the-highs entries is the VUKE-class fix codified.
    cap_at_watch = False
    if range_pct is not None:
        if range_pct < 35:
            score += 1
            reasons.append(f"Near annual lows ({range_pct:.0f}th pctile)")
        elif range_pct >= 80:
            score = max(0, score - 2)
            reasons.append(
                f"At 52w highs ({range_pct:.0f}th pctile) — capped at WATCH"
            )
            cap_at_watch = True
        elif range_pct >= 65:
            score = max(0, score - 1)
            reasons.append(
                f"Near 52w highs ({range_pct:.0f}th pctile) — limited swing upside"
            )

    score = max(0, min(8, score))
    signal = _grade(score)
    if cap_at_watch and signal == "BUY":
        signal = "WATCH"

    entry_note = _swing_entry_note(rsi, off_52w, has_catalyst, range_pct)
    return HorizonVerdict(
        signal=signal, score=f"{score}/8", horizon="1-8 weeks",
        reasons=reasons, entry_note=entry_note, raw_score=score,
    )


def _swing_entry_note(
    rsi: float, off_52w: float, has_catalyst: bool, range_pct: float | None,
) -> str | None:
    """Concrete next-action prompt — what would unlock a swing entry?"""
    if range_pct is not None and range_pct >= 65:
        return (
            f"Wait for a pullback. Genuine swing entry zone: 35th-pctile or "
            f"lower of the 52w range, ideally with RSI < 38."
        )
    if not has_catalyst:
        return "Need a specific catalyst (earnings, product launch, macro) before entry."
    if rsi >= 50:
        return "Wait for RSI < 40 — current zone is too neutral for a real bounce."
    if off_52w < 5:
        return "Need ≥5% off the 52w high for a meaningful entry."
    return None


def _score_long_term(
    *, sharpe: float, pe_flag: str, analyst_upside: float,
    cagr: float, momentum_3m: float,
) -> HorizonVerdict:
    """Spec §4.2. Quality + value + analyst conviction over 6-18mo."""
    score = 0
    reasons: list[str] = []

    # 1 — 5-year Sharpe
    if sharpe > 0.7:
        score += 2
        reasons.append(f"Sharpe {sharpe:.2f}")
    elif sharpe > 0.5:
        score += 1
        reasons.append(f"Sharpe {sharpe:.2f}")

    # 2 — Valuation vs basket (proxy for vs-history until snapshot
    # store lands; see TRADEPRO-SPEC-001 §10 Q1).
    if pe_flag == "CHEAP":
        score += 2
        reasons.append("Cheap vs basket peers")
    elif pe_flag == "FAIR":
        score += 1

    # 3 — Analyst consensus upside
    if analyst_upside > 25:
        score += 2
        reasons.append(f"{analyst_upside:.0f}% analyst upside")
    elif analyst_upside > 15:
        score += 1
        reasons.append(f"{analyst_upside:.0f}% analyst upside")

    # 4 — 5-year CAGR
    if cagr > 10:
        score += 1
        reasons.append(f"CAGR {cagr:.1f}%")

    # 5 — Recent earnings / momentum trajectory
    if momentum_3m > 0:
        score += 1

    score = max(0, min(8, score))
    return HorizonVerdict(
        signal=_grade(score), score=f"{score}/8",
        horizon="6-18 months", reasons=reasons, raw_score=score,
        entry_note=(
            "Solid hold at any reasonable entry — quality + valuation"
            " trump entry timing on this horizon."
            if score >= _BAND_BUY else None
        ),
    )


def _score_passive(
    *, expense_ratio: float, n_holdings: int | None, sharpe: float,
    cagr: float, div_yield: float, legal_type: str,
) -> HorizonVerdict:
    """Spec §4.3. DCA-friendly score over 3-5 years.

    Per spec §10 Q3: individual stocks (n_holdings == 1) return a
    distinct N/A envelope rather than a misleading 0/8 AVOID. The
    thinking is that a single stock isn't a passive vehicle by
    definition — directing capital there over a 5-year DCA horizon
    needs the long-term horizon framework, not this one.
    """
    is_single_stock = (
        n_holdings == 1 or legal_type in {"EQUITY", "COMMON STOCK"}
    )
    if is_single_stock:
        return HorizonVerdict(
            signal="N/A", score="N/A",
            horizon="3-5 years",
            reasons=["Single-stock — see Long-term horizon for hold case"],
            entry_note=(
                "Passive accumulation needs broad diversification. "
                "Use a market-index ETF (e.g. ACWI, VWRL) for a 3-5y "
                "DCA programme; treat individual names via Long-term."
            ),
        )

    score = 0
    reasons: list[str] = []

    # 1 — Expense ratio (cost compounds over decades)
    if expense_ratio < 0.1:
        score += 2
        reasons.append(f"{expense_ratio:.2f}% OCF")
    elif expense_ratio < 0.3:
        score += 1
        reasons.append(f"{expense_ratio:.2f}% OCF")

    # 2 — Number of holdings (diversification)
    if n_holdings is not None and n_holdings > 200:
        score += 2
        reasons.append(f"{n_holdings} holdings")
    elif n_holdings is not None and n_holdings > 50:
        score += 1
        reasons.append(f"{n_holdings} holdings")

    # 3 — 5-year Sharpe
    if sharpe > 0.6:
        score += 2
        reasons.append(f"Sharpe {sharpe:.2f}")
    elif sharpe > 0.4:
        score += 1

    # 4 — 5-year CAGR
    if cagr > 7:
        score += 1
        reasons.append(f"CAGR {cagr:.1f}%")

    # 5 — Dividend yield (compounding floor)
    if div_yield > 1.5:
        score += 1
        reasons.append(f"{div_yield:.1f}% yield")

    score = max(0, min(8, score))
    return HorizonVerdict(
        signal=_grade(score), score=f"{score}/8",
        horizon="3-5 years", reasons=reasons, raw_score=score,
        entry_note=(
            "DCA monthly regardless of price. Reinvest dividends."
            if score >= _BAND_BUY else None
        ),
    )
