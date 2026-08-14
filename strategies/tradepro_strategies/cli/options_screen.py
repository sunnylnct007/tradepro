"""tradepro-options-screen — the wheel CANDIDATE SCREEN job.

For each approved underlying it computes, on the gateway's connection:
  • IV-Rank   — 52w OPTION_IMPLIED_VOLATILITY (the vega-edge gate)
  • regime    — Ichimoku-cloud position on daily bars (constructive vs breaking)
  • falling-knife — recent sharp breakdown
  • chain     — near-month put at ~0.27 delta (OI / spread / premium) [LIVE only]
then runs it ALL through the options risk engine and POSTs the screen to
/api/options/candidates for the Options tab.

NO FALSE POSITIVES: anything that can't be computed (chain unavailable outside
market hours, no IV history, …) flows to the risk engine as None → BLOCK with a
visible reason → eligible=False. A weekend run therefore shows IV-Rank + regime
but "pending market open" on the live-chain gates — honest, not a fake green.

Run:  TRADEPRO_IBKR_PORT=7500 uv run tradepro-options-screen
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from ..quant_engine.options.iv_rank import fetch_iv_rank
from ..quant_engine.options.risk import (
    MarketContext, OptionsRiskConfig, PortfolioState, Regime, Structure,
    TradeCandidate, evaluate,
)

log = logging.getLogger("tradepro.options_screen")

# Wheel universe — EVIDENCE-SELECTED from a 2023→2026 wheel backtest (return on
# capital-at-risk vs the ~4.5%/yr bank rate). The winners are mid-vol QUALITY
# names (energy / utility / healthcare / staples): they beat the bank ~2× with
# low drawdown. Dropped vs the old BRD list: KO/T/JPM (under the bank — too
# low-vol, premiums too thin), INTC/PFE (beat bank but −50%+ drawdown). Add
# DUK/D for defensive yield. Re-run tradepro-wheel-backtest to revisit.
#   CVX 12.2%/-7%  ABBV 11.7%/-16%  XOM 11.1%/-8%  VZ 10.3%/-11%
#   DUK 8.8%/-8%   JNJ 6.3%/-10%    MO 5.7%/-10%   PG 5.0%/-13%
# Wheel universe — liquid option chains, quality underlyings you'd accept
# assignment on, weighted toward strikes that FIT the configurable pot
# (TRADEPRO_WHEEL_PER_POSITION_GBP; a $315 JPM strike needs a raised cap).
# Expanded 10 → 30 (user: "only 14 symbols — we should evaluate more").
DEFAULT_UNIVERSE = [
    # original core
    "CVX", "XOM", "ABBV", "JNJ", "VZ", "MO", "PG", "DUK", "D", "PEP",
    # affordable, liquid chains (fit a £10k/pos pot)
    "KO", "T", "PFE", "F", "INTC", "BAC", "WFC", "CSCO", "MU", "GM",
    "SLB", "OXY", "KMI", "DVN", "GILD", "BMY", "CMCSA", "DOW", "WMB", "HPE",
    # mega-liquid chains — the deepest/tightest option markets there are. Their
    # strikes only fit a RAISED pot (TRADEPRO_WHEEL_PER_POSITION_GBP): the
    # notional gate decides affordability per the user's configured capital,
    # the universe just makes them CANDIDATES (config-driven, not pre-filtered).
    "NVDA", "GOOGL", "AAPL", "MSFT", "AMD", "QCOM",
    # expansion 36 → 66 (owner 2026-08-09: "we need more symbols to compare").
    # Same bar: liquid chains, names you'd accept assignment on.
    # financials / healthcare / consumer / tech / energy / industrials
    "IBM", "JPM", "C", "USB", "SCHW", "MRK", "CVS", "TGT", "SBUX", "NKE",
    "KHC", "MDLZ", "ORCL", "DELL", "HPQ", "HAL", "FCX", "NEM", "DAL", "UPS", "ON",
    # ETFs — natural wheel underlyings: deep chains and STRUCTURALLY no
    # earnings event inside any expiry window (see _ETF_UNDERLYINGS).
    "XLE", "XLF", "XLI", "XLU", "GDX", "SLV", "TLT", "IWM", "KRE",
    # owner's IBKR "TradePro-Screen" watchlist merge (10 Aug 2026 — "is the
    # list based on my IBKR watchlist?" — it is now): the equities from that
    # watchlist not already above. Watchlist edits still need a manual sync
    # here (auto-sync = future work; the MCP watchlist API is session-side).
    "ACN", "TSLA", "GS", "MS", "META", "UBER", "DIS", "HOOD", "MRVL",
    "APLD", "AMZN", "PLTR", "IBKR",
    # index ETFs (owner 11 Aug 2026: "add index on the option wheel screen").
    # ETF form, NOT SPX-style index options — those are cash-settled/European
    # so they can't assign shares, which breaks the wheel's assignment leg.
    # SPY/QQQ strikes only fit a raised per-position pot; the notional gate
    # reports that honestly rather than pre-filtering them out.
    "SPY", "QQQ", "DIA",
]

# ETFs have no earnings — the blackout gate gets a structural False, not a
# "calendar unavailable" block. Keep in sync with the ETF rows above.
_ETF_UNDERLYINGS = frozenset({
    "XLE", "XLF", "XLI", "XLU", "GDX", "SLV", "TLT", "IWM", "KRE",
    "SPY", "QQQ", "GLD", "EEM", "HYG", "DIA",
})

_FX_GBPUSD = 1.27  # BRD display rate; USD strike×100 → GBP notional


def _next_confirmed_earnings(symbol: str) -> "tuple[datetime.date | None, bool]":
    """(next_upcoming_date, store_answered) from the central store.

    CONFIRMED means eventCount ≥ 1 from the bulk-harvested calendar (SPEC
    §1.1: 'not merely no event found'). (None, True) = an AUTHORITATIVE
    store verified there is no upcoming report in the horizon — a real
    answer. (None, False) = the store couldn't answer (empty/stale/down);
    the caller falls back or blocks. Either None means the short tier is
    NOT admissible — its premise is a confirmed date."""
    import datetime as _d
    try:
        from ..earnings import _calendar_store_events, _store_is_authoritative
        from .push_to_api import load_credentials
        base, _tok = load_credentials()
        data = _calendar_store_events(symbol, base)
        if not data or not _store_is_authoritative(data.get("store")):
            return None, False
        today = _d.date.today()
        future = sorted(
            _d.date.fromisoformat(str(ev["report_date"])[:10])
            for ev in data.get("events") or []
            if ev.get("report_date")
            and _d.date.fromisoformat(str(ev["report_date"])[:10]) > today)
        return (future[0] if future else None), True
    except Exception:  # noqa: BLE001 — no store answer = no confirmed date
        return None, False


def _earnings_in_window(symbol: str, dte: int) -> bool | None:
    """Is an earnings date within the option's expiry window (today..+dte)?
    STORE-FIRST (13 Aug 2026): the bulk-harvested calendar is the confirmed
    source — a verified absence answers False, a confirmed date answers the
    comparison; yfinance stays as fallback only when the store can't answer.
    Returns None when neither can → the risk engine BLOCKs (no false
    positive — never sell premium we can't clear for earnings)."""
    import datetime as _d
    nxt, store_answered = _next_confirmed_earnings(symbol)
    if store_answered:
        if nxt is None:
            return False   # verified absence in the store's horizon
        return nxt <= _d.date.today() + _d.timedelta(days=dte)
    try:
        import datetime as _d
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        dates = []
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                dates = ed if isinstance(ed, list) else [ed]
        if not dates:
            return None
        today = _d.date.today()
        end = today + _d.timedelta(days=dte)
        for d in dates:
            dd = d if isinstance(d, _d.date) else getattr(d, "date", lambda: None)()
            if dd and today <= dd <= end:
                return True
        return False
    except Exception:  # noqa: BLE001
        return None


def sane_csp_pick(abs_delta: float | None, strike: float | None, spot: float | None) -> bool:
    """Sanity assertion on a suggested CSP strike (external review 9 Aug 2026,
    owner-approved: WMB rendered a Δ0.92 put 13% ITM as a 'suggestion' — a
    synthetic long, not a CSP). Sparse/stale chains can make nearest-to-0.27
    selection land on garbage; a pick is only render-worthy when it is OTM
    (strike < spot) with |delta| in 0.15–0.40. Anything else = NO suggestion
    (the row then blocks honestly on 'delta unavailable / no usable strike')."""
    if abs_delta is None or strike is None or spot is None or spot <= 0:
        return False
    return (0.15 <= abs_delta <= 0.40) and strike < spot


def _mid(vals: list[float], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    w = vals[i - n + 1: i + 1]
    return (max(w) + min(w)) / 2.0


def empirical_assignment_risk(closes: list[float], *, otm_pct: float,
                              dte: int) -> dict | None:
    """How often has THIS underlying actually fallen further than the strike
    is out-of-the-money, over a window this long? (owner, 13 Aug 2026: "drill
    on convincing numbers — how we decided that one".)

    Model deltas give a risk-neutral probability; this gives the FACT: every
    overlapping `dte`-calendar-day window in the cached history, counted.
    Also reports how deep the breaches went, because assignment frequency
    without assignment depth is only half the risk. None when history is too
    thin to say anything honest. Pure; unit-tested."""
    c = [x for x in closes if x and x > 0]
    span = max(int(round(dte * 252 / 365)), 1)      # calendar days → sessions
    if len(c) < span + 120:
        return None
    rets = [(c[i + span] / c[i] - 1.0) for i in range(len(c) - span)]
    if not rets:
        return None
    threshold = -abs(otm_pct)
    breaches = [r for r in rets if r <= threshold]
    depths = sorted(abs(r - threshold) * 100 for r in breaches)
    worst = min(rets)
    return {
        "windows_tested": len(rets),
        "window_sessions": span,
        "breach_pct": round(len(breaches) / len(rets) * 100, 1),
        "median_breach_depth_pct": (round(depths[len(depths) // 2], 1) if depths else None),
        "worst_window_pct": round(worst * 100, 1),
        "formula": (f"count of {span}-session windows with return ≤ {threshold * 100:.1f}% "
                    f"÷ {len(rets)} windows = {len(breaches)}/{len(rets)} = "
                    f"{len(breaches) / len(rets) * 100:.1f}%"),
        "why": ("historical frequency of assignment at this distance — the empirical "
                "counterpart to delta. Depth matters too: when it breached, it went a "
                "median " + (f"{depths[len(depths) // 2]:.1f}% further" if depths else "n/a")
                + " past the strike."),
    }


def decision_trace(*, eligible: bool, blocks: list[str], warnings: list[str],
                   cfg, delta: float | None, dte: int, oi: int | None,
                   premium: float | None, strike: float | None,
                   spread: float | None, iv_rank: float | None,
                   iv_hv: float | None, regime: str | None,
                   notional_gbp: float | None) -> list[dict]:
    """Gate-by-gate ledger: threshold, actual, verdict — so "why is this not
    a candidate" is answerable without reading prose. Mirrors the equity
    decision-trace pattern. A gate whose input is missing reads UNKNOWN, which
    is a BLOCK for the wheel (never sell premium on an input we can't see)."""
    def _row(name, actual, threshold, ok, unit=""):
        return {"gate": name, "actual": actual, "threshold": threshold, "unit": unit,
                "verdict": ("pass" if ok else "fail") if actual is not None else "unknown"}
    ann = ((premium / strike) * (365.0 / dte) * 100
           if (premium and strike and dte > 0) else None)
    spread_pct = (spread / premium * 100) if (spread is not None and premium) else None
    return [
        _row("delta band", None if delta is None else round(delta, 3),
             f"{cfg.delta_min}–{cfg.delta_max}",
             delta is not None and cfg.delta_min <= delta <= cfg.delta_max),
        _row("DTE band", dte, f"{cfg.dte_min}–{cfg.dte_max}",
             cfg.dte_min <= dte <= cfg.dte_max, "days"),
        _row("vega edge (IV-Rank)", iv_rank, f"≥ {cfg.iv_rank_min}",
             iv_rank is not None and iv_rank >= cfg.iv_rank_min),
        _row("vega edge (IV/HV bridge)", iv_hv, f"≥ {cfg.iv_hv_min}",
             iv_hv is not None and iv_hv >= cfg.iv_hv_min),
        _row("premium floor", premium, f"≥ ${cfg.min_premium_usd}",
             premium is not None and premium >= cfg.min_premium_usd, "$/share"),
        _row("annualised yield", None if ann is None else round(ann, 1),
             f"≥ {cfg.min_ann_yield_pct}", ann is not None and ann >= cfg.min_ann_yield_pct, "%/yr"),
        _row("open interest", oi, f"≥ {cfg.oi_min}", oi is not None and oi >= cfg.oi_min),
        _row("spread vs mid", None if spread_pct is None else round(spread_pct, 1),
             f"≤ {cfg.spread_max_pct_of_mid * 100:.0f}",
             spread_pct is not None and spread_pct <= cfg.spread_max_pct_of_mid * 100, "%"),
        _row("regime", regime, "GREEN or YELLOW",
             regime in ("GREEN", "YELLOW")),
        _row("position size", notional_gbp, f"≤ £{cfg.per_position_gbp:,.0f}",
             notional_gbp is not None and notional_gbp <= cfg.per_position_gbp, "£"),
    ]


def explain_calcs(*, symbol: str, spot: float | None, strike: float | None,
                  premium: float | None, dte: int, contracts: int = 1,
                  bid: float | None = None, ask: float | None = None,
                  spread: float | None = None, iv: float | None = None,
                  hv30: float | None = None, delta: float | None = None,
                  nav_gbp: float | None = None, fx: float = _FX_GBPUSD,
                  rate: float | None = None, div_yield: float | None = None) -> dict:
    """Every derived figure on a row, with the ARITHMETIC that produced it
    (owner, 13 Aug 2026: "options is all about parameters and calculations —
    mostly factual, unlike equity. The usability of that screen is key, with
    all figures explained and backed by calculations").

    Each entry is {value, formula, why}: `formula` substitutes the ACTUAL
    inputs so a reader can redo the sum by hand and catch us if it's wrong.
    Figures whose inputs are missing are omitted entirely — never rendered
    with a guessed input. Pure function; unit-tested."""
    import math as _m
    out: dict[str, dict] = {}
    mult = 100 * max(1, contracts)
    r = rate if rate is not None else float(os.environ.get("TRADEPRO_RISK_FREE_RATE", "0.04"))

    if premium is not None and strike and strike > 0 and dte > 0:
        ann = (premium / strike) * (365.0 / dte) * 100
        out["annualised_yield_pct"] = {
            "value": round(ann, 1),
            "formula": (f"premium ÷ strike × 365 ÷ DTE × 100 = "
                        f"{premium:.2f} ÷ {strike:g} × 365 ÷ {dte} × 100 = {ann:.1f}%"),
            "why": "income per unit of collateral, annualised — compare against the ~4% "
                   "the same cash earns idle.",
        }
    if premium is not None:
        out["max_profit_usd"] = {
            "value": round(premium * mult, 2),
            "formula": f"premium × 100 × contracts = {premium:.2f} × 100 × {contracts} = ${premium * mult:,.2f}",
            "why": "kept in full if the put expires out-of-the-money.",
        }
    if premium is not None and strike:
        be = strike - premium
        out["breakeven_and_basis"] = {
            "value": round(be, 2),
            "formula": f"strike − premium = {strike:g} − {premium:.2f} = {be:.2f}",
            "why": "if assigned, this is your effective cost per share — the price you "
                   "are agreeing to own it at.",
        }
        out["max_loss_usd"] = {
            "value": round(be * mult, 2),
            "formula": f"(strike − premium) × 100 × contracts = {be:.2f} × 100 × {contracts} = ${be * mult:,.2f}",
            "why": "worst case, if the underlying went to zero before expiry.",
        }
    if strike and spot and spot > 0:
        otm = (spot - strike) / spot * 100
        out["otm_distance_pct"] = {
            "value": round(otm, 1),
            "formula": f"(spot − strike) ÷ spot × 100 = ({spot:.2f} − {strike:g}) ÷ {spot:.2f} × 100 = {otm:.1f}%",
            "why": "how far the underlying must fall before assignment matters.",
        }
    if strike:
        coll = strike * mult
        out["collateral"] = {
            "value": round(coll / fx, 0),
            "formula": (f"strike × 100 × contracts ÷ FX = {strike:g} × 100 × {contracts} "
                        f"÷ {fx} = £{coll / fx:,.0f}  (${coll:,.0f})"),
            "why": "cash locked up for the whole holding period — the real cost of the trade.",
        }
        if nav_gbp and nav_gbp > 0:
            pct = coll / fx / nav_gbp * 100
            out["size_vs_nav_pct"] = {
                "value": round(pct, 1),
                "formula": f"collateral ÷ NAV × 100 = £{coll / fx:,.0f} ÷ £{nav_gbp:,.0f} × 100 = {pct:.1f}%",
                "why": "concentration: how much of the account one position ties up.",
            }
    if spread is not None and premium:
        sp = spread / premium * 100
        out["spread_pct_of_premium"] = {
            "value": round(sp, 1),
            "formula": (f"(ask − bid) ÷ mid × 100 = ({ask:.2f} − {bid:.2f}) ÷ {premium:.2f} × 100 = {sp:.1f}%"
                        if (bid is not None and ask is not None)
                        else f"spread ÷ mid × 100 = {spread:.2f} ÷ {premium:.2f} × 100 = {sp:.1f}%"),
            "why": "round-trip friction as a share of the credit — work the limit, don't "
                   "cross the whole spread.",
        }
    if iv and hv30 and hv30 > 0:
        ratio = iv / hv30
        out["iv_hv_ratio"] = {
            "value": round(ratio, 3),
            "formula": f"IV ÷ HV30 = {iv:.1%} ÷ {hv30:.1%} = {ratio:.3f}",
            "why": "the vega edge: >1 means options are pricing MORE movement than the "
                   "stock has actually delivered — that gap is what selling premium harvests.",
        }
    if spot and dte > 0:
        q = div_yield or 0.0
        fwd = spot * _m.exp((r - q) * dte / 365.0)
        out["forward_price"] = {
            "value": round(fwd, 2),
            "formula": (f"S × e^((r − q) × DTE ÷ 365) = {spot:.2f} × e^(({r:.3f} − {q:.3f}) "
                        f"× {dte} ÷ 365) = {fwd:.2f}"),
            "why": "where the market prices the underlying AT expiry — the honest anchor "
                   "for how far OTM a strike really is."
                   + ("" if div_yield is not None else " (no dividend yield served → q=0, "
                      "so this is slightly overstated for payers)"),
        }
    if delta is not None:
        out["delta"] = {
            "value": round(delta, 3),
            "formula": f"|Δ| = {abs(delta):.3f} (broker-served greek)",
            "why": "rough odds of finishing in-the-money — 0.27Δ ≈ a ~27% chance of assignment.",
        }
    return out


def fetch_portfolio_state(nav_gbp: float | None = None) -> tuple["PortfolioState", dict]:
    """The REAL book → PortfolioState (13 Aug 2026).

    Until now both evaluate() call sites passed `PortfolioState()` — all
    zeros — so three live gates were INERT: the deploy ceiling, the
    max-positions cap, and the drawdown brakes. Every candidate was judged as
    if the desk were empty, which is also why the screen could not answer the
    owner's "do I want 7% of NAV in one metal" / "28% in one theme"
    questions.

    Returns (state, summary). On any failure the state is EMPTY and the
    summary says so — the caller surfaces that loudly rather than silently
    reverting to the old always-empty behaviour."""
    from ..quant_engine.options.risk import PortfolioState
    summary = {"available": False, "reason": None, "open_symbols": [],
               "deployed_gbp": 0.0, "open_positions": 0,
               "realised_loss_gbp": 0.0, "deployed_pct_of_nav": None}
    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        r = requests.get(f"{base.rstrip('/')}/api/options/positions", timeout=20,
                         headers={"Authorization": f"Bearer {tok}"} if tok else {})
        r.raise_for_status()
        rows = (r.json() or {}).get("positions") or []
    except Exception as e:  # noqa: BLE001
        summary["reason"] = f"book unreachable ({e}) — capital gates cannot be enforced this run"
        return PortfolioState(), summary

    open_rows = [p for p in rows if (p.get("state") or "").upper() not in ("CLOSED", "EXPIRED")]
    deployed = sum(float(p.get("cash_secured_gbp") or 0.0) for p in open_rows)
    # Banked LOSSES only (positive number = total loss), per the brake contract.
    losses = -sum(min(float(p.get("realised_pnl_gbp") or 0.0), 0.0) for p in rows)
    summary.update({
        "available": True,
        "open_symbols": sorted({(p.get("symbol") or "").upper() for p in open_rows}),
        "deployed_gbp": round(deployed, 0),
        "open_positions": len(open_rows),
        "realised_loss_gbp": round(losses, 0),
        "deployed_pct_of_nav": (round(deployed / nav_gbp * 100, 1)
                                if (nav_gbp and nav_gbp > 0) else None),
    })
    return PortfolioState(deployed_gbp=deployed, open_positions=len(open_rows),
                          cumulative_realised_loss_gbp=losses), summary


def hv_gap_diagnostics(closes: list[float], *, window: int = 30) -> dict | None:
    """Is the trailing-HV window contaminated by ONE gap day — and when does
    it roll off? (owner, 13 Aug 2026, the IBM case: HV read 85.7% purely
    because a −25% print on 14 July sat at the very edge of the 30-day
    window; a day later HV collapses toward ~30-35% and IV/HV leaps 0.40 →
    ~1.0 with nothing having changed.)

    This is the MIRROR of the wheel-backtest flaw where the same proxy
    SPIKED after META's gap and overstated premium — post-gap, trailing
    realised vol misleads in both directions for about a month.

    Returns None when the window is clean or too short. Otherwise: the raw
    HV, the HV with that single observation removed, the gap's size/date,
    and how many sessions until it leaves the window. Pure — no gating
    decision here; the caller surfaces it and the human decides."""
    import math as _m
    c = [x for x in closes if x and x > 0]
    if len(c) < window + 2:
        return None
    rets = [_m.log(c[k] / c[k - 1]) for k in range(len(c) - window, len(c))]
    if len(rets) < window:
        return None

    def _ann(vals: list[float]) -> float | None:
        if len(vals) < 5:
            return None
        mean = sum(vals) / len(vals)
        var = sum((r - mean) ** 2 for r in vals) / (len(vals) - 1)
        return _m.sqrt(var) * _m.sqrt(252.0)

    j = max(range(len(rets)), key=lambda k: abs(rets[k]))
    biggest = rets[j]
    others = sorted(abs(r) for k, r in enumerate(rets) if k != j)
    median_abs = others[len(others) // 2] if others else 0.0
    # A GAP, not just the window's largest wiggle: ≥8% in a day, and ≥4×
    # the window's typical move. Both, so a quietly trending name never
    # trips it.
    if not (abs(biggest) >= 0.08 and median_abs > 0 and abs(biggest) >= 4 * median_abs):
        return None
    hv_raw = _ann(rets)
    hv_ex = _ann([r for k, r in enumerate(rets) if k != j])
    if hv_raw is None or hv_ex is None:
        return None
    # Sessions until the gap leaves the trailing window (1 = tomorrow).
    sessions_until_rolloff = j + 1
    return {
        "contaminated": True,
        "gap_return_pct": round(biggest * 100, 1),
        "gap_sessions_ago": len(rets) - j,
        "sessions_until_rolloff": sessions_until_rolloff,
        "hv_raw": round(hv_raw, 4),
        "hv_ex_gap": round(hv_ex, 4),
        "note": (f"HV {hv_raw:.0%} is inflated by a single {biggest * 100:+.0f}% session "
                 f"{len(rets) - j} sessions ago; excluding it HV is {hv_ex:.0%}. "
                 f"It rolls out of the 30d window in {sessions_until_rolloff} session(s), "
                 f"so this vega read will move mechanically — re-check then."),
    }


def _short_tier_cfg(cfg: "OptionsRiskConfig") -> "OptionsRiskConfig":
    """TIER_SHORT gate overrides (SPEC §1.2) — stricter to pay for gamma.
    Env-tunable like every other knob; defaults are the spec's, calibrated so
    the MRVL Aug21'26 200P @ $2.90 (0.28Δ, 58.6%/yr, 9 DTE) is admissible."""
    from dataclasses import replace as _rep
    def _f(k: str, d: float) -> float:
        try: return float(os.environ.get(k, d))
        except (TypeError, ValueError): return d
    # dte_max defaults to (standard dte_min - 1) so the two tiers ABUT with no
    # gap. The spec wrote 7-21 against a 25-50 band, leaving 22-24 DTE
    # unreachable: the ORCL Sep04 case (22 DTE, clears the 7-Sep print by 3
    # days) fell in that hole — while a 9-DTE MRVL trade, which carries MORE
    # gamma, was admissible. The dead zone was an artefact of two independently
    # chosen numbers, not a risk judgement. Revert with
    # TRADEPRO_WHEEL_SHORT_DTE_MAX=21.
    return _rep(
        cfg,
        dte_min=int(_f("TRADEPRO_WHEEL_SHORT_DTE_MIN", 7)),
        dte_max=int(_f("TRADEPRO_WHEEL_SHORT_DTE_MAX", max(cfg.dte_min - 1, 7))),
        delta_max=_f("TRADEPRO_WHEEL_SHORT_DELTA_MAX", 0.30),
        min_ann_yield_pct=_f("TRADEPRO_WHEEL_SHORT_MIN_ANN_YIELD_PCT", 25.0),
        min_premium_usd=_f("TRADEPRO_WHEEL_SHORT_MIN_PREMIUM_USD", 0.50),
        oi_min=int(_f("TRADEPRO_WHEEL_SHORT_OI_MIN", 500)),
        spread_max_pct_of_mid=_f("TRADEPRO_WHEEL_SHORT_SPREAD_MAX_PCT", 0.12),
    )


def _evaluate_short_tier(sym: str, cfg, ivr, regime, falling_knife, ref_close,
                         earnings_date, nav_gbp, iv_vol_pctile, portfolio=None) -> dict:
    """TIER_SHORT candidate (SPEC §1) — earnings-avoidance only, never
    yield-chasing. Called ONLY when the standard band conflicts with a
    CONFIRMED earnings date. Picks the latest weekly expiry inside
    [7, 21] DTE that clears the print by ≥ 3 XNYS sessions, then evaluates
    with the stricter short-tier gates. Every outcome returns a dict with an
    explicit `status` — the why-not column must distinguish data conditions
    from market ones (SPEC §1.3)."""
    import datetime as _d
    from ..gates.earnings_proximity import sessions_between
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    from ..quant_engine.options.chains import select_by_abs_delta, delta_of
    from ..quant_engine.options.chains_g3 import fetch_chain_g3

    clear_sessions = int(float(os.environ.get("TRADEPRO_WHEEL_SHORT_EARNINGS_CLEAR_SESSIONS", 3)))
    scfg = _short_tier_cfg(cfg)
    today = _d.date.today()

    # Probe the month around the short window for its listed weeklies.
    probe = fetch_chain_g3(sym, target_dte=(scfg.dte_min + scfg.dte_max) // 2, right="P")
    if probe is None or not probe.available_expiries:
        return {"status": "no_chain_for_short_window",
                "detail": "G3 served no chain/expiries for the 7-21 DTE month"}
    ok_expiries = []
    for e in probe.available_expiries:
        ed = _d.date.fromisoformat(e)
        dte = (ed - today).days
        if not (scfg.dte_min <= dte <= scfg.dte_max):
            continue
        gap = sessions_between(ed, earnings_date)
        if gap is not None and gap >= clear_sessions:
            ok_expiries.append((dte, e, gap))
    if not ok_expiries:
        return {"status": "no_clearing_expiry",
                "detail": (f"no listed expiry in {scfg.dte_min}-{scfg.dte_max} DTE clears "
                           f"earnings {earnings_date} by ≥{clear_sessions} trading days")}
    dte_pick, expiry_pick, gap = max(ok_expiries)

    chain = (probe if probe.expiry == expiry_pick
             else fetch_chain_g3(sym, expiry=expiry_pick, right="P"))
    if chain is None or not chain.puts or chain.spot <= 0:
        return {"status": "no_chain_for_short_window",
                "detail": f"chain fetch failed for expiry {expiry_pick}"}
    _r = float(os.environ.get("TRADEPRO_RISK_FREE_RATE", "0.04"))
    pricer = BlackScholesPricer(risk_free_rate=_r,
                                dividend_yield=(ivr.div_yield or 0.0) if ivr.available else 0.0)
    t = max(dte_pick, 1) / 365.0
    q = select_by_abs_delta(chain.puts, 0.27, chain.spot, t, pricer)
    if q is None:
        return {"status": "no_suitable_strike", "detail": "no put near the delta band"}
    strike = q.strike
    delta = abs(delta_of(q, chain.spot, t, pricer))
    premium = q.mid if q.mid > 0 else None
    if premium is None:
        return {"status": "no_live_premium", "detail": "short-tier legs quoteless right now"}
    spread = q.spread if (q.bid > 0 and q.ask > 0) else None
    notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
    ann_yield = round((premium / strike) * (365.0 / dte_pick) * 100, 1) if strike > 0 else None
    if not sane_csp_pick(delta, strike, ref_close or chain.spot):
        return {"status": "no_suitable_strike", "detail": "insane pick rejected (sparse chain)"}

    ctx = MarketContext(
        regime=Regime(regime) if regime else None,
        falling_knife=falling_knife,
        iv_rank=ivr.iv_rank if ivr.available else None,
        iv_hv_ratio=ivr.iv_hv_ratio if ivr.available else None,
        iv_rank_window_days=ivr.days if ivr.available else None,
        open_interest=q.open_interest, bid_ask_spread_usd=spread,
        premium_mid_usd=premium,
        # The tier's whole point: expiry CLEARS the print (by ≥3 sessions).
        earnings_in_expiry_window=False,
        data_fresh=True, quotes_delayed=False,
    )
    cand = TradeCandidate(symbol=sym, structure=Structure.CASH_SECURED_PUT,
                          abs_delta=delta, dte=dte_pick, strike=strike,
                          notional_gbp=notional_gbp)
    decision = evaluate(cand, ctx, portfolio or PortfolioState(), scfg)
    # The vol-regime floor applies to short-tier bridge passes too.
    if (ivr.available and ivr.iv_rank is None and ivr.iv_hv_ratio is not None
            and iv_vol_pctile is not None
            and iv_vol_pctile < float(os.environ.get("TRADEPRO_WHEEL_MIN_VOL_REGIME_PCTILE", "15"))):
        from dataclasses import replace as _rep
        decision = _rep(decision, allowed=False,
                        blocks=list(decision.blocks) + [
                            f"IV at the {iv_vol_pctile:.0f}th pctile of this name's 1y vol range "
                            f"— thin absolute premium (short tier inherits the floor)."])
    return {
        "status": "eligible" if decision.allowed else "blocked",
        "badge": "SHORT-DATED",
        "reason": (f"{dte_pick} DTE — clears {sym} earnings {earnings_date.isoformat()} "
                   f"by {gap} trading days"),
        "expiry": expiry_pick, "dte": dte_pick,
        "suggested_strike": strike, "suggested_delta": round(delta, 3),
        "suggested_premium": premium, "open_interest": q.open_interest,
        "spread_usd": spread, "annualized_yield_pct": ann_yield,
        "notional_gbp": notional_gbp,
        "eligible": decision.allowed,
        "blocks": decision.blocks, "warnings": decision.warnings,
    }


def vol_regime_percentile(closes: list[float], iv: float, *, hv_window: int = 30) -> float | None:
    """Percentile of the CURRENT IV within the symbol's own trailing-year
    distribution of rolling 30d realised vol — the ABSOLUTE-premium sanity
    companion the IV/HV bridge lacks (the KRE contradiction, 12 Aug 2026:
    bridge 1.35 — strongest vega read yet — while IV sat at the 2.4th
    percentile of its year, because realised vol collapsed FASTER than
    implied; the ratio certifies edge on the year's thinnest premium).
    Computed from cached daily closes — no new feed. None when history is
    too thin (<120 rolling points) — unknown, never fabricated. Pure."""
    import math as _m
    c = [x for x in closes if x and x > 0]
    if iv is None or iv <= 0 or len(c) < hv_window + 120:
        return None
    rets = [_m.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    vols: list[float] = []
    for i in range(hv_window, len(rets) + 1):
        w = rets[i - hv_window:i]
        mean = sum(w) / hv_window
        var = sum((r - mean) ** 2 for r in w) / (hv_window - 1)
        vols.append(_m.sqrt(var) * _m.sqrt(252.0))
    vols = vols[-252:]
    if len(vols) < 120:
        return None
    below = sum(1 for v in vols if v <= iv)
    return round(below / len(vols) * 100.0, 1)


def regime_from_closes(closes: list[float]) -> tuple[str | None, bool | None]:
    """Ichimoku-cloud regime from daily closes (newest last). Constructive =
    price above the cloud (GREEN), in the cloud (YELLOW); breaking = below cloud
    (ORANGE); sharp recent breakdown = RED. Returns (regime, falling_knife);
    (None, None) when there isn't enough history to decide (→ risk BLOCKs)."""
    c = [x for x in closes if x and x > 0]
    if len(c) < 60:
        return None, None
    i = len(c) - 1
    tenkan = _mid(c, i, 9)
    kijun = _mid(c, i, 26)
    span_a = (tenkan + kijun) / 2 if (tenkan and kijun) else None
    span_b = _mid(c, i, 52)
    if span_a is None or span_b is None:
        return None, None
    cloud_top, cloud_bot = max(span_a, span_b), min(span_a, span_b)
    price = c[-1]
    # Falling knife: a sharp recent drop (≥10% over ~5 sessions) AND below cloud.
    ret_5d = (c[-1] / c[-6] - 1.0) if len(c) >= 6 else 0.0
    falling_knife = (ret_5d <= -0.10) and (price < cloud_bot)
    if falling_knife:
        return Regime.RED.value, True
    if price > cloud_top:
        return Regime.GREEN.value, False
    if price >= cloud_bot:
        return Regime.YELLOW.value, False
    return Regime.ORANGE.value, False


def _fetch_nav_gbp() -> float | None:
    """Account NAV in GBP, best-effort, for the size-fit annotation (v1
    §F0.3-4: "contract notional vs NAV vs target allocation" — INFORMATIONAL
    only; per project_wheel_signal_vs_paper_capital_split candidates are
    NEVER capital-gated here, the risk engine's own notional cap already
    handles hard sizing). Reads the unified cross-broker account-state table
    (backend/TradePro.Api IntegrationsEndpoints, /integrations/account-state)
    and sums netLiquidation across accounts. IBKR reports in its account base
    currency (USD for the paper account this screen targets); converted to
    GBP with the same _FX_GBPUSD rate the rest of this file already uses for
    strike notional, so the two numbers are comparable. None on ANY failure
    — size-fit just doesn't render rather than showing a wrong number."""
    try:
        from . import push_to_api as _pta
        import requests
        base, tok = _pta.load_credentials()
        r = requests.get(f"{base}/api/integrations/account-state",
                          headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        r.raise_for_status()
        accounts = (r.json() or {}).get("accounts") or []
        total_usd = sum(a.get("netLiquidation") or 0 for a in accounts)
        return round(total_usd / _FX_GBPUSD, 0) if total_usd else None
    except Exception as e:  # noqa: BLE001
        log.warning("NAV fetch failed (size-fit will be omitted): %s", e)
        return None


def build_carry_map(stored_payload: dict | None, *, now=None, max_age_h: float | None = None) -> dict:
    """Symbol → last-priced row from the PREVIOUS screen push, for the
    carry-forward pricing tier (owner, 10 Aug 2026: a dark-MD run must not
    collapse the board to 'premium unavailable' when we priced it hours ago).

    A row qualifies when it carries a premium from a real source (live_mid /
    prev_close_indicative) or is itself a still-fresh carry — carries CHAIN
    across runs because each push overwrites the stored payload, so the
    original pricing timestamp travels in `premium_as_of_utc` and the age
    cap is enforced against THAT, never against the latest push time.
    Pure function; unit-tested."""
    from datetime import datetime as _dt, timezone as _tz
    if not stored_payload:
        return {}
    cap_h = max_age_h if max_age_h is not None else float(
        os.environ.get("TRADEPRO_WHEEL_CARRY_MAX_AGE_H", "96"))
    now = now or _dt.now(_tz.utc)
    fallback_asof = stored_payload.get("generated_at_utc")
    carry: dict = {}
    for row in stored_payload.get("candidates") or []:
        sym = row.get("symbol")
        src = row.get("premium_source")
        if not sym or row.get("suggested_premium") is None or row.get("suggested_strike") is None:
            continue
        if src not in ("live_mid", "prev_close_indicative", "carried_last_live"):
            continue
        asof = row.get("premium_as_of_utc") or fallback_asof
        if not asof:
            continue
        try:
            asof_dt = _dt.fromisoformat(str(asof).replace("Z", "+00:00"))
        except ValueError:
            continue
        age_h = (now - asof_dt.astimezone(_tz.utc)).total_seconds() / 3600.0
        if age_h < 0 or age_h > cap_h:
            continue
        carry[sym] = {**row, "premium_as_of_utc": asof_dt.isoformat(), "_carry_age_h": age_h}
    return carry


def _screen_symbol(ib, ib_insync, sym: str, cfg: OptionsRiskConfig, market_open: bool, nav_gbp: float | None = None,
                   carry_row: dict | None = None, portfolio: "PortfolioState | None" = None,
                   book: dict | None = None) -> dict:
    """Build one candidate row: IV-Rank + regime + (live) chain → risk engine.

    `ib` may be None (Gateway unreachable) — IV-Rank then fails closed
    (available=False, visible reason) rather than attempting its own
    connection per symbol; regime and chain don't need it at all any more
    (bar-cache and G3 respectively), so the screen still produces real,
    honest candidates in that mode — just always BLOCKed on the IV-rank
    gate, which is the correct behaviour when that gate's input is
    genuinely unavailable, not a crash.
    """
    # IV metrics — OAuth Web API FIRST (owner decision 2026-08-09: no local
    # Gateway; code must be runnable off-Mac). fetch_iv_rank_web snapshots
    # current IV + HV30, grows the options_iv_daily dataset, and serves
    # IV-Rank from OUR history once the window matures (IV/HV bridge until
    # then). The Gateway path survives only as a fallback when a connected
    # `ib` happens to exist (it serves a true 52w rank immediately).
    from ..quant_engine.options.iv_rank import fetch_iv_rank_web
    ivr = fetch_iv_rank_web(sym)
    if not ivr.available and ib is not None:
        ivr = fetch_iv_rank(sym, ib=ib)

    # Daily closes for the regime (Ichimoku on the bar cache — yfinance-
    # backed, the same source get_market_state/the digest already use.
    # No Gateway needed: this used to require an ib_insync TRADES bars
    # request, which is exactly the session-contention dependency G3 was
    # built to remove from the chain fetch — removing it here too so
    # regime works even when no Gateway is reachable at all.
    regime = falling_knife = ref_close = None
    closes: list[float] = []
    try:
        from datetime import timedelta
        from ..cache import ensure_cached
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=400)
        prices = ensure_cached("yahoo", sym, start, end)
        close_col = "adj_close" if "adj_close" in prices.columns else "close"
        closes = prices[close_col].dropna().tolist()
        regime, falling_knife = regime_from_closes(closes)
        if closes and closes[-1] > 0:
            ref_close = closes[-1]
    except Exception as e:  # noqa: BLE001 — None → risk BLOCKs (no false positive)
        log.warning("%s regime fetch failed: %s", sym, e)

    # HV fallback from our own cached closes: IBKR's snapshot HV (field 7284)
    # is dark for ETFs, which killed the IV/HV bridge for exactly the names
    # with no earnings risk. 30d realised vol from the SAME daily closes the
    # regime just used is honest, real math on GOOD data — labeled as the
    # computed variant so nobody mistakes it for the broker-served figure.
    if (ivr.available and ivr.iv and ivr.iv_hv_ratio is None and len(closes) >= 31):
        import math as _math
        rets = [_math.log(closes[i] / closes[i - 1])
                for i in range(len(closes) - 30, len(closes)) if closes[i - 1] > 0]
        if len(rets) >= 20:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            hv = _math.sqrt(var) * _math.sqrt(252.0)
            if hv > 0:
                from dataclasses import replace as _dc_replace
                ivr = _dc_replace(ivr, hv30=round(hv, 5),
                                  iv_hv_ratio=round(ivr.iv / hv, 3),
                                  reason=ivr.reason + " (HV computed from cached closes)")

    # Options chain — G3 FIRST (TradePro's own API-hosted chain feed, real
    # broker delta/greeks/OI, OAuth REST session — no local Gateway needed),
    # then ib_insync/TWS IBKR (real model greeks too, but needs a reachable
    # local Gateway — the session-contention path G3 exists to avoid), then
    # yfinance as last resort (delayed/patchy; its IV is often missing → BS
    # delta ≈ 0 → nothing clears the band). Select the put nearest 0.27 delta
    # (BRD strike rule).
    oi = strike = delta = premium = notional_gbp = None
    premium_source = None   # 'live_mid' | 'prev_close_indicative'
    spread = bid = ask = None
    dte = 35
    chain_ok = False
    chain_source = None
    chain_iv = None         # the selected quote's own IV — feeds the model cross-check
    spot_divergence = None  # |yf spot / IBKR close − 1| when both known
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    from ..quant_engine.options.chains import fetch_chain, select_by_abs_delta, delta_of
    from ..quant_engine.options.chains_g3 import fetch_chain_g3
    from ..quant_engine.options.chains_ibkr import fetch_chain_ibkr
    # Forward-aware BSM: r from env (bank/risk-free proxy), q = the name's own
    # dividend yield when the snapshot served one (field 7286; None → 0, never
    # guessed). On this dividend-heavy universe pricing off spot instead of
    # the forward skews the fallback put deltas — the Google OTM→ITM lesson.
    _r = float(os.environ.get("TRADEPRO_RISK_FREE_RATE", "0.04"))
    pricer = BlackScholesPricer(risk_free_rate=_r,
                                dividend_yield=(ivr.div_yield or 0.0) if ivr.available else 0.0)

    # 0) G3 — TradePro's own chain feed (backend/TradePro.Api ChainEndpoints).
    try:
        gc = fetch_chain_g3(sym, target_dte=35, right="P")
        if gc and gc.puts and gc.spot > 0:
            t = max(gc.dte, 1) / 365.0
            q = select_by_abs_delta(gc.puts, 0.27, gc.spot, t, pricer)
            if q is not None:
                dte = gc.dte
                strike = q.strike
                delta = abs(delta_of(q, gc.spot, t, pricer))
                oi = q.open_interest
                spread = q.spread if (q.bid > 0 and q.ask > 0) else None
                premium = q.mid if q.mid > 0 else None
                premium_source = 'live_mid' if premium else None
                # Between sessions options have NO live quote (no pre-market)
                # — fall back to the PRIOR-SESSION close (G3 field 7741),
                # LABELED indicative. Owner rule: run with last available data.
                if premium is None and q.prior_close and q.prior_close > 0:
                    premium = q.prior_close
                    premium_source = 'prev_close_indicative'
                bid = q.bid if q.bid > 0 else None
                ask = q.ask if q.ask > 0 else None
                chain_iv = q.iv if q.iv and q.iv > 0 else None
                notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
                chain_ok = True
                chain_source = "g3"
                if ref_close is None:
                    ref_close = gc.spot
    except Exception as e:  # noqa: BLE001 — fall through to ib_insync/yfinance
        log.warning("%s G3 chain failed, trying ib_insync: %s", sym, e)

    # 1) IBKR via ib_insync/TWS (authoritative, needs local Gateway). Reuse the
    # screen's connection + IBKR close as spot. Skipped when G3 already worked.
    # ONLY when the screen holds a live Gateway connection — fetch_chain_ibkr
    # self-connects when ib is None, and with the Gateway retired (owner 9 Aug:
    # OAuth-only) that's a guaranteed ConnectionRefused + timeout per symbol.
    if not chain_ok and ib is not None:
        try:
            ic = fetch_chain_ibkr(sym, target_dte=35, ib=ib, pricer=pricer, spot=ref_close)
            if ic and ic.puts and ic.spot > 0:
                t = max(ic.dte, 1) / 365.0
                q = select_by_abs_delta(ic.puts, 0.27, ic.spot, t, pricer)
                if q is not None:
                    dte = ic.dte
                    strike = q.strike
                    delta = abs(delta_of(q, ic.spot, t, pricer))
                    oi = q.open_interest
                    spread = q.spread if (q.bid > 0 and q.ask > 0) else None
                    premium = q.mid if q.mid > 0 else None
                    premium_source = 'live_mid' if premium else None
                    bid = q.bid if q.bid > 0 else None
                    ask = q.ask if q.ask > 0 else None
                    chain_iv = q.iv if q.iv and q.iv > 0 else None
                    notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
                    chain_ok = True
                    chain_source = "ibkr"
        except Exception as e:  # noqa: BLE001 — fall through to yfinance
            log.warning("%s IBKR chain failed, trying yfinance: %s", sym, e)

    # 2) yfinance fallback (only if IBKR gave nothing usable).
    if not chain_ok:
        try:
            chain = fetch_chain(sym, target_dte=35, pricer=pricer)
            # Spot-sanity guard: reject a chain whose spot disagrees with the
            # authoritative IBKR close by >15% (e.g. yfinance "$134" for INTC).
            spot_ok = True
            if chain and chain.spot > 0 and ref_close:
                spot_divergence = abs(chain.spot / ref_close - 1.0)
                if spot_divergence > 0.15:
                    spot_ok = False
                    log.warning("%s yf spot %.2f diverges %.0f%% from IBKR close %.2f — rejecting",
                                sym, chain.spot, spot_divergence * 100, ref_close)
            if chain and chain.puts and chain.spot > 0 and spot_ok:
                dte = chain.dte
                t = max(dte, 1) / 365.0
                q = select_by_abs_delta(chain.puts, 0.27, chain.spot, t, pricer)
                if q is not None:
                    strike = q.strike
                    delta = abs(delta_of(q, chain.spot, t, pricer))
                    oi = q.open_interest
                    spread = q.spread if (q.bid > 0 and q.ask > 0) else None
                    premium = q.mid if q.mid > 0 else None
                    premium_source = 'live_mid' if premium else None
                    bid = q.bid if q.bid > 0 else None
                    ask = q.ask if q.ask > 0 else None
                    chain_iv = q.iv if q.iv and q.iv > 0 else None
                    notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
                    chain_ok = True
                    chain_source = "yfinance"
        except Exception as e:  # noqa: BLE001 — None → risk BLOCKs (fail-visible)
            log.warning("%s yfinance chain fetch failed: %s", sym, e)

    # Sanity assertion on the pick (applies to ALL three chain sources): an
    # ITM or out-of-band "suggestion" is worse than none — null the pick so
    # the row blocks honestly instead of rendering a synthetic long as a CSP.
    if strike is not None and not sane_csp_pick(delta, strike, ref_close):
        log.warning(
            "%s: rejecting insane CSP pick (strike %s, |delta| %s, spot %s) — "
            "sparse/stale chain; no suggestion rendered", sym, strike, delta, ref_close)
        strike = delta = premium = oi = spread = bid = ask = chain_iv = None
        notional_gbp = None

    # ── Carry-forward pricing tier (owner priority, 10 Aug 2026) ─────────
    # When live mid AND the option's prior close are BOTH dark (post-close /
    # the one-MD-session contention), adopt the last PRICED pick for this
    # symbol wholesale — strike+premium+OI+spread belong to one snapshot, so
    # we never graft an old premium onto a new strike (the 54→55.5 drift).
    # Labeled with its age and hard-BLOCKed from eligibility below: the
    # board stays informative, the numbers stay non-actionable.
    premium_as_of_utc = (datetime.now(timezone.utc).isoformat()
                         if premium is not None else None)
    carry_age_h = None
    if premium is None and carry_row:
        c_strike = carry_row.get("suggested_strike")
        c_delta = carry_row.get("suggested_delta")
        if sane_csp_pick(c_delta, c_strike, ref_close or carry_row.get("ref_close")):
            strike = c_strike
            delta = c_delta
            premium = carry_row.get("suggested_premium")
            oi = carry_row.get("open_interest")
            spread = carry_row.get("spread_usd")
            bid = carry_row.get("bid")
            ask = carry_row.get("ask")
            chain_iv = None   # not re-verifiable — model cross-check stays off
            dte = carry_row.get("dte") or dte
            notional_gbp = round(strike * 100 / _FX_GBPUSD, 0) if strike else None
            premium_source = "carried_last_live"
            premium_as_of_utc = carry_row.get("premium_as_of_utc")
            carry_age_h = round(float(carry_row.get("_carry_age_h") or 0.0), 1)
            if not chain_source:
                chain_source = carry_row.get("chain_source")
            log.info("%s: live+prior-close premium dark — carried last priced pick "
                     "(%.1fh old, source %s)", sym, carry_age_h,
                     carry_row.get("premium_source"))

    # ETFs have no earnings event — structural False (a fact of the security
    # type), NOT a skipped check. Single names still go through the calendar
    # lookup and BLOCK when it can't be verified.
    earnings_in_window = False if sym in _ETF_UNDERLYINGS else _earnings_in_window(sym, dte)

    ctx = MarketContext(
        regime=Regime(regime) if regime else None,
        falling_knife=falling_knife,
        iv_rank=ivr.iv_rank if ivr.available else None,
        iv_hv_ratio=ivr.iv_hv_ratio if ivr.available else None,
        iv_rank_window_days=ivr.days if ivr.available else None,
        open_interest=oi, bid_ask_spread_usd=spread,
        premium_mid_usd=premium,   # scales the spread cap (relative, not $0.10 flat)
        earnings_in_expiry_window=earnings_in_window,
        # Screen on best-available data: a usable chain is enough to ASSESS
        # eligibility (execution still needs live quotes at the open).
        data_fresh=chain_ok,
        # IBKR chains are DELAYED until OPRA is enabled → the spread gate becomes
        # an advisory warning (candidates surface for paper, marked indicative)
        # instead of blocking on a stale wide spread. Flip to False once OPRA is
        # on (then real-time spreads hard-block again).
        quotes_delayed=(chain_source == "ibkr"
                        or premium_source in ("prev_close_indicative", "carried_last_live")),
    )
    cand = TradeCandidate(symbol=sym, structure=Structure.CASH_SECURED_PUT,
                          abs_delta=delta, dte=dte, strike=strike, notional_gbp=notional_gbp)
    decision = evaluate(cand, ctx, portfolio or PortfolioState(), cfg)
    # Same-underlying duplicate: the book already carries this name. Not a
    # hard block (rolling/adding is a legitimate choice) but the screen must
    # SAY it — the owner's concentration question starts here.
    _already = bool(book and sym in (book.get("open_symbols") or []))
    if _already:
        from dataclasses import replace as _dc_dup
        decision = _dc_dup(decision, warnings=list(decision.warnings) + [
            f"Book already holds an open {sym} position — this would ADD to that "
            f"exposure, not diversify it."])
    # Vol-regime floor for BRIDGE passes (the KRE contradiction): a ratio
    # pass with the absolute vol level in the bottom of the name's own
    # yearly range is positive edge on very little money — and typically
    # exactly when the name sits near its highs. Rank-based passes are
    # exempt (a real 52w IV-rank already IS the absolute measure).
    iv_vol_pctile = (vol_regime_percentile(closes, ivr.iv)
                     if (ivr.available and ivr.iv) else None)
    # Gap-contaminated HV check (the IBM case) — the bridge ratio can be
    # mechanically wrong in EITHER direction for ~30 days after a gap.
    hv_gap = hv_gap_diagnostics(closes) if closes else None
    if hv_gap and ivr.available and ivr.iv:
        _hv_ex = hv_gap.get("hv_ex_gap")
        hv_gap["iv_hv_ratio_ex_gap"] = (round(ivr.iv / _hv_ex, 3)
                                        if (_hv_ex and _hv_ex > 0) else None)
    _vega_gate_val = ("rank" if (ivr.available and ivr.iv_rank is not None)
                      else "bridge" if (ivr.available and ivr.iv_hv_ratio is not None)
                      else None)
    _vol_floor = float(os.environ.get("TRADEPRO_WHEEL_MIN_VOL_REGIME_PCTILE", "15"))
    if (_vega_gate_val == "bridge" and iv_vol_pctile is not None
            and iv_vol_pctile < _vol_floor):
        from dataclasses import replace as _dc_rep2
        decision = _dc_rep2(
            decision, allowed=False,
            blocks=list(decision.blocks) + [
                f"IV at the {iv_vol_pctile:.0f}th percentile of this name's own "
                f"1y vol range (< {_vol_floor:.0f} floor) — the IV/HV bridge "
                f"passes only because realised vol collapsed faster; selling "
                f"the year's thinnest premium is edge on very little money."],
        )
    # Gap-contaminated HV: WARN, never silently re-gate. The gate keeps using
    # the raw HV (no false positives from a judgement call about trimming an
    # observation), but the row now says the read is about to move on its own
    # — and by how much — so "look again in N sessions" is printed, not
    # guessed. Mirrors the backtest's declared IV-proxy caveat.
    if hv_gap:
        from dataclasses import replace as _dc_rep_hv
        _extra = hv_gap["note"]
        _ex_ratio = hv_gap.get("iv_hv_ratio_ex_gap")
        if _ex_ratio is not None:
            _extra += f" IV/HV excluding the gap ≈ {_ex_ratio}."
        decision = _dc_rep_hv(decision, warnings=list(decision.warnings) + [_extra])

    # Carried pricing is informative, never actionable: hard-block eligibility
    # so a stale number can't be crowned best / starred / auto-recorded
    # (NO FALSE POSITIVES). The economics stay visible on the row.
    if premium_source == "carried_last_live":
        from dataclasses import replace as _dc_rep
        decision = _dc_rep(
            decision, allowed=False,
            blocks=list(decision.blocks) + [
                f"Pricing carried from the last priced screen ({carry_age_h}h old) — "
                f"indicative only; not actionable until live quotes return."],
        )

    # Annualised premium yield — the income metric that ranks "best to trade".
    # premium ÷ strike (capital per share) scaled to a year by DTE. Only
    # meaningful with a real premium + strike (None pre-market → unranked).
    ann_yield_pct = None
    if premium and strike and strike > 0 and dte > 0:
        ann_yield_pct = round((premium / strike) * (365.0 / dte) * 100, 1)

    # Put-vs-buy side-by-side (v1 §F0.3-4) — the two ways to get long this
    # name. Selling the CSP: collect premium now; if assigned, effective cost
    # basis = strike - premium. If not assigned, the premium is pure income.
    # Only rendered with a real spot AND a real premium+strike — never a
    # fabricated comparison from a partial chain.
    put_vs_buy = None
    if ref_close and premium and strike:
        effective_cost = strike - premium
        put_vs_buy = {
            "buy_now_price": round(ref_close, 2),
            "sell_put_strike": strike,
            "sell_put_premium": round(premium, 2),
            "sell_put_effective_cost_if_assigned": round(effective_cost, 2),
            "discount_vs_buy_now_pct": round((ref_close - effective_cost) / ref_close * 100, 1),
        }

    # Size-fit — contract notional as a share of account NAV. Informational
    # only (see _fetch_nav_gbp docstring); None when NAV wasn't reachable.
    size_fit_pct = None
    if notional_gbp and nav_gbp:
        size_fit_pct = round(notional_gbp / nav_gbp * 100, 1)

    # Model cross-check (the pricer's documented job (b): sanity-check the
    # quoted mid vs BSM fair value so a stale/fat-fingered chain can't drive
    # the signal). Priced at the QUOTE's own IV with the forward-aware pricer
    # (r from env, q = dividend yield) — only when every input is real.
    model_price = model_vs_mid_pct = None
    if premium and strike and chain_iv and ref_close and dte > 0:
        try:
            model_price = round(pricer.price(ref_close, strike, max(dte, 1) / 365.0,
                                             chain_iv, "put"), 3)
            if model_price > 0:
                model_vs_mid_pct = round((premium - model_price) / model_price * 100, 1)
        except Exception:  # noqa: BLE001 — a failed model check renders as n/a, never a guess
            model_price = model_vs_mid_pct = None

    spread_pct_of_mid = (round(spread / premium * 100, 1)
                         if (spread is not None and premium and premium > 0) else None)

    # Forward price at expiry — F = S·e^((r−q)T). THE honest anchor for "how
    # far OTM is this strike really": dividend payers' forwards sit below
    # spot, so a strike that looks 5% OTM vs spot is closer at expiry. Basis
    # is labeled: without a served dividend yield the forward is rates-only
    # (slightly overstated for payers) — shown, never silently wrong.
    forward_price = forward_basis = None
    if ref_close and dte > 0:
        import math as _m
        _q = ivr.div_yield if (ivr.available and ivr.div_yield is not None) else None
        forward_price = round(ref_close * _m.exp((_r - (_q or 0.0)) * dte / 365.0), 2)
        forward_basis = "r_and_div_yield" if _q is not None else "r_only_div_yield_unavailable"

    # ── TIER_SHORT (SPEC §1) — earnings-avoidance only ───────────────────
    # Attempted ONLY when the standard band conflicts with earnings (the
    # MRVL case: Sep04 holds through the 27-Aug print, Aug21 clears it).
    # The two unavailability states are deliberately distinct (§1.3): a
    # missing confirmed date is a DATA condition, not a market one.
    short_tier = None
    if earnings_in_window is True and sym not in _ETF_UNDERLYINGS:
        _e_date, _store_ok = _next_confirmed_earnings(sym)
        if _e_date is None:
            short_tier = {"status": "unavailable_no_confirmed_earnings",
                          "detail": ("standard band conflicts with earnings but the store has "
                                     "no CONFIRMED date"
                                     + ("" if _store_ok else " (store couldn't answer)"))}
        else:
            try:
                short_tier = _evaluate_short_tier(
                    sym, cfg, ivr, regime, falling_knife, ref_close,
                    _e_date, nav_gbp, iv_vol_pctile, portfolio)
            except Exception as e:  # noqa: BLE001 — short tier must never kill the row
                log.warning("%s: short-tier evaluation failed: %s", sym, e)
                short_tier = {"status": "error", "detail": str(e)[:200]}

    return {
        "symbol": sym,
        "tier": "standard",
        "already_in_book": bool(book and sym in (book.get("open_symbols") or [])),
        "short_tier": short_tier,
        "regime": regime,
        "iv_rank": round(ivr.iv_rank, 1) if (ivr.available and ivr.iv_rank is not None) else None,
        "iv": round(ivr.iv, 4) if (ivr.available and ivr.iv is not None) else None,
        "iv_hv_ratio": ivr.iv_hv_ratio if ivr.available else None,
        "iv_rank_days": ivr.days if ivr.available else None,
        "vega_gate": _vega_gate_val,
        # Current IV's percentile within the name's own 1y realised-vol
        # distribution — the absolute-level context next to the ratio.
        "iv_vol_regime_pctile": iv_vol_pctile,
        # Gap-contamination diagnostics for the bridge ratio (None = clean).
        "hv_gap": hv_gap,
        "open_interest": oi,
        # Concrete quote parameters (owner 2026-08-09: "quote a few technical
        # parameters and price needs to be checked to make it concrete") —
        # the raw buy/sell numbers behind every suggested trade.
        "bid": bid,
        "ask": ask,
        "spread_usd": spread,
        "spread_pct_of_mid": spread_pct_of_mid,
        "model_price": model_price,
        "model_vs_mid_pct": model_vs_mid_pct,
        "div_yield": ivr.div_yield if ivr.available else None,
        "forward_price": forward_price,
        "forward_basis": forward_basis,
        "eligible": decision.allowed,
        "blocks": decision.blocks,
        "warnings": decision.warnings,
        "suggested_strike": strike,
        "suggested_delta": delta,
        "suggested_premium": premium,
        "premium_source": premium_source,
        # When the pricing was carried, WHEN it was actually priced (the
        # T-1/T-2 label the desk renders) + how old it is in hours.
        "premium_as_of_utc": premium_as_of_utc,
        "premium_age_h": carry_age_h,
        "dte": dte,
        "annualized_yield_pct": ann_yield_pct,
        "chain_source": chain_source,
        "ref_close": round(ref_close, 2) if ref_close else None,
        "spot_divergence_pct": round(spot_divergence * 100, 1) if spot_divergence is not None else None,
        "put_vs_buy": put_vs_buy,
        # Drill-down: gate ledger + this name's OWN history of breaching a
        # strike this far away — the empirical counterpart to delta.
        "decision_trace": decision_trace(
            eligible=decision.allowed, blocks=decision.blocks, warnings=decision.warnings,
            cfg=cfg, delta=delta, dte=dte, oi=oi, premium=premium, strike=strike,
            spread=spread, iv_rank=(ivr.iv_rank if ivr.available else None),
            iv_hv=(ivr.iv_hv_ratio if ivr.available else None), regime=regime,
            notional_gbp=notional_gbp),
        "history_check": (empirical_assignment_risk(
            closes, otm_pct=((ref_close - strike) / ref_close if (ref_close and strike) else 0.05),
            dte=dte) if closes else None),
        # Show-your-working: every derived figure with its arithmetic.
        "calcs": explain_calcs(
            symbol=sym, spot=ref_close, strike=strike, premium=premium, dte=dte,
            bid=bid, ask=ask, spread=spread,
            iv=(ivr.iv if ivr.available else None),
            hv30=(ivr.hv30 if ivr.available else None),
            delta=delta, nav_gbp=nav_gbp,
            div_yield=(ivr.div_yield if ivr.available else None)),
        "size_fit_pct": size_fit_pct,
    }


def screen_data_health(rows: list[dict], market_open: bool) -> dict:
    """Grade the RUN itself (owner rule 2026-08-09: 'if it's missing some
    dataset we should make it loud and clear'). A data problem must never be
    distinguishable from a market verdict only by reading 66 rows — this
    rolls the run's data gaps into one loud, honest summary the UI banners.
    Pure function, unit-tested."""
    n = len(rows) or 1
    iv_dark = sum(1 for r in rows if r.get("vega_gate") is None)
    no_chain = sum(1 for r in rows if not r.get("chain_source"))
    no_premium = sum(1 for r in rows if r.get("suggested_premium") is None)
    # IMPORTANT (owner 2026-08-09): "even today we should have a good Friday
    # snapshot from IBKR" — correct, and today's runs proved it (real Friday-
    # close premiums/OI on many names). So MARKET CLOSED IS NOT AN EXCUSE for
    # dark fields: off-hours, IBKR still serves the last session's snapshot.
    # Gaps are graded as DATA problems to chase, never waved off as "closed".
    degraded = (iv_dark > n * 0.2) or (no_premium > n * 0.3) or (no_chain > n * 0.2)
    reasons = []
    if iv_dark:
        reasons.append(f"{iv_dark}/{len(rows)} symbols have NO vega-edge data (IV snapshot dark)")
    if no_chain:
        reasons.append(f"{no_chain}/{len(rows)} symbols returned no usable chain from any provider")
    if no_premium:
        reasons.append(f"{no_premium}/{len(rows)} symbols have no premium quote")
    ctx_note = ("market CLOSED — screened on the LAST-AVAILABLE session's data; "
                "IBKR still serves that snapshot, so the gaps listed are DATA "
                "issues to fix, not market absence" if not market_open else "market OPEN")
    return {
        "degraded": degraded,
        "iv_dark_count": iv_dark,
        "no_chain_count": no_chain,
        "no_premium_count": no_premium,
        "symbols": len(rows),
        # Framing matters (owner): the screen DID run on last-available data
        # and the verdicts below stand on what was verifiable — the banner
        # names what's missing, it does not mean "nothing ran".
        "summary": (f"Screened on last-available data with GAPS ({ctx_note}): "
                    + "; ".join(reasons)
                    + ". Rows using verified data stand; the listed gaps block only their own gates "
                    + "and re-check on the next run.")
                   if degraded and reasons else
                   f"Data healthy: {len(rows)} symbols screened ({ctx_note}).",
    }


def _maybe_send_wheel_email(payload: dict, prev_payload: dict | None) -> bool:
    """Email the wheel board when the ELIGIBLE set CHANGED vs the previous
    push — a new ⭐ appearing (or one dying) is the actionable moment the
    owner asked to hear about (11 Aug 2026: "why no email for the option
    wheeler"). At most one mail per screen run (3-4 runs/day), reusing the
    nightly digest's SMTP creds (~/.tradepro/email-creds.json). Disable with
    TRADEPRO_WHEEL_EMAIL=0. Fail-soft: an email problem must never fail the
    screen — it logs to run_log and moves on. Returns True when a mail went."""
    if os.environ.get("TRADEPRO_WHEEL_EMAIL", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    now_elig = {c["symbol"] for c in payload.get("candidates") or [] if c.get("eligible")}
    prev_elig = {c["symbol"] for c in (prev_payload or {}).get("candidates") or [] if c.get("eligible")}
    # SHORT-DATED eligibles participate in change detection with a distinct
    # key so a new short-tier trade (the MRVL class) alerts even when the
    # standard set is unchanged.
    now_short = {f"{c['symbol']}·SHORT" for c in payload.get("candidates") or []
                 if (c.get("short_tier") or {}).get("eligible")}
    prev_short = {f"{c['symbol']}·SHORT" for c in (prev_payload or {}).get("candidates") or []
                  if (c.get("short_tier") or {}).get("eligible")}
    now_elig |= now_short
    prev_elig |= prev_short
    if now_elig == prev_elig:
        return False
    try:
        from types import SimpleNamespace
        from .email_digest import CRED_PATH, send_email
        import json as _json
        data = _json.loads(CRED_PATH.read_text()) if CRED_PATH.is_file() else {}
        cfg = {
            "smtp_host": data.get("smtp_host") or os.environ.get("TRADEPRO_SMTP_HOST"),
            "smtp_port": int(data.get("smtp_port") or os.environ.get("TRADEPRO_SMTP_PORT") or 465),
            "smtp_user": data.get("smtp_user") or os.environ.get("TRADEPRO_SMTP_USER"),
            "smtp_password": data.get("smtp_password") or os.environ.get("TRADEPRO_SMTP_PASSWORD"),
            "from": data.get("from") or os.environ.get("TRADEPRO_EMAIL_FROM"),
            "to": [t for t in (data.get("to") or [os.environ.get("TRADEPRO_EMAIL_TO")]) if t],
        }
        gained = sorted(now_elig - prev_elig)
        lost = sorted(prev_elig - now_elig)
        best = payload.get("best_symbol")
        lines = []
        for c in payload.get("candidates") or []:
            if c["symbol"] in now_elig:
                star = "⭐ " if c["symbol"] == best else "   "
                lines.append(
                    f"{star}{c['symbol']}: ${c.get('suggested_strike')} put · "
                    f"${c.get('suggested_premium')} premium ({c.get('premium_source')}) · "
                    f"{c.get('annualized_yield_pct')}%/yr · Δ{c.get('suggested_delta')} · "
                    f"OI {c.get('open_interest')} · {c.get('dte')}d · regime {c.get('regime')}")
            st = c.get("short_tier") or {}
            if st.get("eligible"):
                lines.append(
                    f"   {c['symbol']} [SHORT-DATED]: ${st.get('suggested_strike')} put "
                    f"{st.get('expiry')} · ${st.get('suggested_premium')} premium · "
                    f"{st.get('annualized_yield_pct')}%/yr · Δ{st.get('suggested_delta')} · "
                    f"OI {st.get('open_interest')} · {st.get('reason')}")
        dh = payload.get("data_health") or {}
        change = []
        if gained:
            change.append(f"NEW eligible: {', '.join(gained)}")
        if lost:
            change.append(f"no longer eligible: {', '.join(lost)}")
        subject = (f"TradePro Wheel — {len(now_elig)} eligible"
                   + (f" · best {best}" if best else "")
                   + (f" · {change[0]}" if change else ""))
        text = "\n".join([
            "Wheel screen update — the eligible set changed.",
            "; ".join(change),
            "",
            *(lines or ["(no eligible candidates on this run)"]),
            "",
            f"Data health: {dh.get('summary', 'n/a')}",
            f"Run: {payload.get('generated_at_utc')} · market_open={payload.get('market_open')}",
            "",
            "Board: http://16.60.201.137/ → Options tab",
        ])
        html = "<pre style=\"font-family:monospace\">" + text.replace("<", "&lt;") + "</pre>"
        send_email(SimpleNamespace(subject=subject, text_body=text, html_body=html,
                                   pdf_bytes=None), cfg)
        log.info("wheel email sent: %s", subject)
        try:
            from ..run_log import log_run
            log_run("options-screen", "email", "ok", summary=subject)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as e:  # noqa: BLE001 — email must never fail the screen
        log.warning("wheel email failed (non-fatal): %s", e)
        try:
            from ..run_log import log_run
            log_run("options-screen", "email", "fail", error=str(e))
        except Exception:  # noqa: BLE001
            pass
        return False


def _strategy_board(rows: list[dict]) -> dict:
    """One board, several option strategies, each with its evidence status.

    SELL-PREMIUM (wheel standard + short-dated) is gated and backtested;
    BUY-VOL (earnings straddle) is scanned live but NOT backtested, so it is
    labelled and can never be presented as an equal-confidence candidate.
    Directional structures are declared as absent rather than silently
    missing."""
    import requests as _rq
    sell_std = [r["symbol"] for r in rows if r.get("eligible")]
    sell_short = [r["symbol"] for r in rows if (r.get("short_tier") or {}).get("eligible")]
    buy_vol: list[dict] = []
    straddle_note = None
    try:
        from .push_to_api import load_credentials
        _b, _t = load_credentials()
        d = _rq.get(f"{_b.rstrip('/')}/api/options/straddle-scan/latest", timeout=15).json()
        for row in (d.get("rows") or [])[:10]:
            buy_vol.append({
                "symbol": row.get("symbol"), "reportDate": str(row.get("report_date"))[:10],
                "impliedMovePct": row.get("implied_move_pct"),
                "realizedMedianPct": row.get("realized_median_pct"),
                "edgeRatio": row.get("edge_ratio"), "nPrints": row.get("n_prints"),
                "candidate": row.get("candidate"),
            })
        if not buy_vol:
            straddle_note = "no scan rows yet today (scanner runs 15:15 London)"
    except Exception as e:  # noqa: BLE001 — the wheel board must not depend on it
        straddle_note = f"straddle scan unavailable ({e})"
    return {
        "sell_premium": {
            "evidence": "backtested (WHEEL_BACKTEST_GATES_V2.md — 8/9 gates pass, G4 open)",
            "standard_eligible": sell_std,
            "short_dated_eligible": sell_short,
        },
        "buy_volatility": {
            "evidence": "OBSERVATIONAL — pre-registered gates NOT yet run; never a trade recommendation",
            "note": straddle_note,
            "rows": buy_vol,
        },
        "directional": {
            "evidence": "NOT BUILT — needs a directional view input + its own gates file",
            "rows": [],
        },
    }


def run_screen(symbols: list[str] | None = None) -> dict:
    import ib_insync
    from . import push_to_api as _pta
    from ..paper import market_hours

    # Universe: explicit arg > TRADEPRO_WHEEL_UNIVERSE env (comma-separated,
    # config-driven per feedback_config_driven_no_hardcoding) > curated default.
    env_universe = [s.strip().upper() for s in
                    os.environ.get("TRADEPRO_WHEEL_UNIVERSE", "").split(",") if s.strip()]
    universe = symbols or env_universe or DEFAULT_UNIVERSE
    cfg = OptionsRiskConfig.from_env()   # capital sizing env-tunable (TRADEPRO_WHEEL_*)
    host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
    cid = int(os.environ.get("TRADEPRO_IBKR_DATA_CLIENT_ID", "97"))
    try:
        market_open = market_hours.is_open("us_equity", datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        market_open = False
    nav_gbp = _fetch_nav_gbp()
    # The REAL book — until today both evaluate() call sites got an empty
    # PortfolioState, leaving the deploy ceiling, position cap and drawdown
    # brakes inert. Fetched once per run and threaded into every candidate.
    portfolio, book = fetch_portfolio_state(nav_gbp)
    if not book["available"]:
        log.warning("portfolio state unavailable: %s", book["reason"])
    else:
        log.info("book: %d open, £%.0f deployed (%s%% of NAV), £%.0f banked losses, symbols %s",
                 book["open_positions"], book["deployed_gbp"],
                 book["deployed_pct_of_nav"], book["realised_loss_gbp"],
                 ", ".join(book["open_symbols"]) or "none")

    # Gateway is now OPTIONAL: chain (G3) and regime (bar cache) no longer
    # need it — only IV-Rank still does (no non-Gateway source for 52w
    # OPTION_IMPLIED_VOLATILITY history exists yet). A degraded run without
    # Gateway still produces real regime + real chain data for every symbol,
    # just permanently BLOCKed on the IV-rank gate — honest, not a crash.
    ib = ib_insync.IB()
    connected = False
    # OAuth-only architecture (owner 9 Aug 2026): the local Gateway is retired —
    # chain via G3, IV via the OAuth iv-daily store. The Gateway tier survives
    # behind an OPT-IN flag for a machine that genuinely runs one; default off
    # so every screen run stops paying a 20s connect timeout + error noise.
    if os.environ.get("TRADEPRO_WHEEL_USE_GATEWAY", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            ib.connect(host, port, clientId=cid, timeout=20)
            connected = True
        except Exception as e:  # noqa: BLE001
            log.warning("IBKR Gateway unreachable (%s) — running degraded: "
                        "chain via G3, regime via bar cache, IV-rank via OAuth store", e)
    else:
        log.info("Gateway tier disabled (OAuth-only) — chain via G3, "
                 "IV via OAuth iv-daily store; set TRADEPRO_WHEEL_USE_GATEWAY=1 to re-enable")

    # Previous push → carry-forward map, so a dark-MD run keeps the last
    # priced board (labeled) instead of collapsing to "premium unavailable".
    import requests as _rq
    carry: dict = {}
    _prev: dict | None = None
    try:
        _base, _tok = _pta.load_credentials()
        _prev = _rq.get(f"{_base.rstrip('/')}/api/options/candidates", timeout=15).json()
        carry = build_carry_map(_prev)
        if carry:
            log.info("carry-forward map: %d symbols priced within the age cap", len(carry))
    except Exception as e:  # noqa: BLE001 — no carry is a degraded, not failed, run
        log.warning("carry-forward map unavailable (%s) — dark rows won't carry", e)

    rows = []
    try:
        for sym in universe:
            rows.append(_screen_symbol(ib if connected else None, ib_insync, sym, cfg, market_open, nav_gbp,
                                       carry_row=carry.get(sym), portfolio=portfolio, book=book))
            log.info("screened %s", sym)
    finally:
        if connected:
            ib.disconnect()

    # Crown the single BEST eligible CSP: highest annualised yield, GREEN
    # preferred over YELLOW as a tiebreak. NEVER crown a non-eligible name —
    # if nothing clears every gate, best_symbol is None (no false "best").
    def _rank_key(r: dict) -> tuple:
        regime_rank = 1.0 if r.get("regime") == "GREEN" else 0.0
        return (r.get("annualized_yield_pct") or 0.0, regime_rank)

    eligible_rows = [r for r in rows if r.get("eligible")]
    best = max(eligible_rows, key=_rank_key) if eligible_rows else None
    for r in rows:
        r["is_best"] = bool(best and r["symbol"] == best["symbol"])
    # SHORT-DATED eligibles surface separately and rank BELOW the standard
    # tier (SPEC §1.3): the short tier is an exception path. It may carry
    # the board's only actionable trade (the MRVL case) but never steals ⭐
    # from a standard candidate.
    short_eligible = [r["symbol"] for r in rows
                      if (r.get("short_tier") or {}).get("eligible")]

    data_health = screen_data_health(rows, market_open)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "market_open": market_open,
        "gateway_available": connected,
        "candidates": rows,
        "best_symbol": best["symbol"] if best else None,
        "eligible_count": len(eligible_rows),
        "short_eligible": short_eligible,
        "data_health": data_health,
        # The book the gates were evaluated against — so a reader can see WHY
        # capital gates fired (or that they could not be enforced at all).
        "book": book,
        # ── Multi-strategy board (owner 13 Aug: "see diff strategy ... we can
        # get into option buying as well, especially around earnings"). The
        # wheel SELLS premium; the straddle scanner BUYS it. Both surface here
        # so one screen answers "what could I do today", each labelled with
        # its own evidence status. Straddle rows are OBSERVATIONAL — their
        # pre-registered gates have not been run.
        "strategies": _strategy_board(rows),
    }
    # Central observability (feedback_central_observability_fail_loud): the
    # run's data-health verdict goes to the run log, so a degraded screen is
    # LOUD in ops — not just discoverable by reading 66 UI rows.
    try:
        from ..run_log import log_run
        log_run(
            "options-screen", "screen",
            "degraded" if data_health["degraded"] else "ok",
            error=(data_health["summary"] if data_health["degraded"] else None),
            summary=(f"{len(rows)} screened, {len(eligible_rows)} eligible, "
                     f"best={best['symbol'] if best else 'none'}; "
                     f"iv_dark={data_health['iv_dark_count']}, "
                     f"no_premium={data_health['no_premium_count']}"),
        )
    except Exception:  # noqa: BLE001 — logging must never fail the screen
        pass
    # Push to the API for the Options tab.
    import requests
    base, tok = _pta.load_credentials()
    if base and tok:
        r = requests.post(f"{base.rstrip('/')}/api/options/candidates",
                          json=payload, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        log.info("pushed screen: HTTP %s (%d candidates, %d eligible)",
                 r.status_code, len(rows), sum(1 for x in rows if x["eligible"]))
    # Owner alert on the actionable moment: the eligible set changed vs the
    # previous push (_prev was fetched above for the carry map — same snapshot).
    _maybe_send_wheel_email(payload, _prev if isinstance(_prev, dict) else None)
    return payload


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = run_screen()
    elig = [c["symbol"] for c in p["candidates"] if c["eligible"]]
    print(f"\nOptions screen: {len(p['candidates'])} screened, {len(elig)} eligible "
          f"(market_open={p['market_open']})")
    if p.get("best_symbol"):
        b = next(c for c in p["candidates"] if c["symbol"] == p["best_symbol"])
        print(f"  ⭐ BEST: {b['symbol']} CSP ${b['suggested_strike']} — "
              f"{b['annualized_yield_pct']}% annualised, {b['regime']}")
    else:
        print("  ⭐ BEST: none — no candidate clears every gate right now")
    for c in p["candidates"]:
        mark = "⭐" if c.get("is_best") else ("✓" if c["eligible"] else "·")
        ivr = f"{c['iv_rank']:.0f}%" if c["iv_rank"] is not None else "n/a"
        yld = f"{c['annualized_yield_pct']:.0f}%/yr" if c.get("annualized_yield_pct") else ""
        print(f"  {mark} {c['symbol']:5} regime={c['regime'] or 'n/a':6} IV-Rank={ivr:>4} {yld:>7} "
              f"{'' if c['eligible'] else '— ' + '; '.join(c['blocks'][:2])}")
    return 0
