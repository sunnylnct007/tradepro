"""Options Income Engine — defined-risk put credit spreads (INCOME_PUT_SPREADS_V1).

Spec: strategies/INCOME_PUT_SPREADS_V1.md (owner's other agent, 23 Nov 2026,
committed verbatim). Stages A–F (gates, expiry/strike, live pricing,
probabilities, sizing, ranking) and H (monitor) live here. Stage G — the IBKR
order instruction — is the other agent's; TradePro never creates or submits
an order on the live account.

Configuration = settings-kv `income_engine`; DEFAULTS below are §2 verbatim.
Every candidate carries the number each gate saw. A candidate is never shown
unless every gate was COMPUTED and passed; a gate we could not compute is a
rejection with reason (`stale_quote`, `no_bars`, `earnings_unknown`…), never
a pass.

Pure functions (gates, strike selection, pricing, probabilities, sizing,
score) take plain inputs so the §9 acceptance tests replay the ABBV trade
from §10 without a broker.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger("tradepro.income_spreads")

STRATEGY = "income_put_spreads"
SPEC = "INCOME_PUT_SPREADS_V1"
SETTINGS_KEY = "income_engine"
RECORD_KEY = "income_put_spreads_manual_trades"   # the ledger (settings-kv, never the repo)
ARTIFACT_UNIVERSE = "income_put_spreads"

#: §2, verbatim. Overridden key-by-key from settings-kv `income_engine`.
DEFAULTS: dict = {
    "capital_gbp": 100_000, "target_annual_return": 0.10,
    "max_loss_per_trade_gbp": 3_000, "max_loss_per_sector_gbp": 6_000,
    "phase_1_total_max_loss_gbp": 6_000,
    "phase_1_exit_condition": "legacy short-put notional <= 50% of 23-Nov-2026 level",
    "universe_mix": {"index_share": 0.5, "single_name_share": 0.5},
    "index_symbols": ["QQQ", "XSP", "IWM"],
    "single_name_seed": ["JPM", "V", "JNJ", "ABBV", "XOM", "ACN", "GOOGL", "PG", "KO", "PEP", "MRK", "UNH", "HD", "CAT"],
    "sectors": {"JPM": "Financials", "V": "Financials", "JNJ": "Health Care", "ABBV": "Health Care",
                "XOM": "Energy", "ACN": "Information Technology", "GOOGL": "Communication Services",
                "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
                "MRK": "Health Care", "UNH": "Health Care", "HD": "Consumer Discretionary", "CAT": "Industrials"},
    "phase": 1,                                  # phase 1: single names only (§8)
    "excluded_sectors_phase_1": ["semiconductors", "memory_storage"],
    "no_two_names_same_sector": True,
    "dte_min": 25, "dte_max": 60,
    "earnings_inside_expiry": "forbidden",
    "require_above_200dma": True, "require_above_ichimoku_cloud": True,
    "falling_knife_10d_return": -0.08,
    "short_strike_below_30d_low": True,
    "short_put_delta_range": [0.15, 0.30],
    # §2 lists [5, 10]; §4.2 takes the first width that works, which would
    # build the golden ABBV example as 270/265. The §9 golden replay demands
    # 270/260, so the acceptance test wins: 10 first. Owner to confirm.
    "spread_width_preference": [10, 5],
    "min_credit_to_width": 0.15, "min_pop_full_credit": 0.70,
    "max_leg_bid_ask_pct": 0.15, "min_open_interest_short_leg": 100,
    "take_profit_pct_of_credit": 0.50,
    "exit_rule": "close next session if underlying CLOSES below the 30d swing low used at entry",
    "avoid_names_already_held": True,
    "fx_source": "ibkr_account_balances",
    "fx_gbp_per_usd_fallback": 1 / 1.31,         # used ONLY when the live rate is unreadable; labelled
    "quote_max_age_s": 60,
    "earnings_store_window_days": 59,          # the API's earnings harvest looks this far ahead
    "score_weights": {"pop": 0.35, "credit": 0.25, "iv_hv": 0.20, "cushion": 0.10, "liquidity": 0.10},
    "max_candidates": 3,
    # Cash-secured put beside every spread (owner, 23 Nov 2026: "the spread
    # decreased the profit… provided cash permits"). Same short strike, no
    # long leg: the whole credit, the whole strike at risk. Sized by CASH, not
    # by the £3k loss cap (which a CSP cannot honour): notional per name and
    # in total, plus the same P(full profit) floor. The owner picks per trade.
    "csp_enabled": True,
    "csp_max_notional_per_name_gbp": 25_000,
    "csp_total_notional_gbp": 50_000,
    "csp_min_pop_full_credit": 0.75,
}


# ── config + ledger (settings-kv) ─────────────────────────────────────────

def _kv_get(base: str, token: str, key: str):
    import requests
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(f"{base.rstrip('/')}/api/settings-kv/{key}", headers=h, timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    j = r.json()
    return j.get("value", j) if isinstance(j, dict) else j


def settings(base: str, token: str) -> dict:
    s = dict(DEFAULTS)
    v = _kv_get(base, token, SETTINGS_KEY)
    if isinstance(v, dict):
        s.update({k: v[k] for k in v if k in DEFAULTS})
        s["source"] = f"settings-kv/{SETTINGS_KEY}"
    else:
        s["source"] = f"defaults (§2; settings-kv/{SETTINGS_KEY} absent)"
    return s


def record(base: str, token: str) -> dict:
    v = _kv_get(base, token, RECORD_KEY)
    return v if isinstance(v, dict) else {"trades": []}


# ── pure math (Stage D) ───────────────────────────────────────────────────

def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probabilities(spot: float, k_short: float, k_long: float, credit: float, dte: int, sigma: float) -> dict:
    """§4.4 — lognormal, risk-neutral, zero drift."""
    if min(spot, k_short, k_long, sigma) <= 0 or dte <= 0:
        return {"p_full_profit": None, "p_loss_any": None, "p_max_loss": None, "one_sd_move_pct": None, "breakeven": None}
    s = sigma * math.sqrt(dte / 365.0)
    be = k_short - credit
    return {"p_full_profit": round(1 - _N(math.log(k_short / spot) / s), 4),
            "p_loss_any": round(_N(math.log(be / spot) / s), 4),
            "p_max_loss": round(_N(math.log(k_long / spot) / s), 4),
            "one_sd_move_pct": round(s, 4), "breakeven": round(be, 2)}


def score(p_full_iv: float, credit_to_width: float, iv_hv: float | None, cushion_pct: float,
          one_sd: float | None, liquidity: float, w: dict) -> float:
    """§4.6."""
    cushion_term = min(cushion_pct / one_sd, 1.0) if one_sd else 0.0
    return round(w["pop"] * p_full_iv
                 + w["credit"] * min(credit_to_width / 0.30, 1.0)
                 + w["iv_hv"] * (min(iv_hv / 1.3, 1.0) if iv_hv else 0.0)
                 + w["cushion"] * cushion_term
                 + w["liquidity"] * liquidity, 4)


# ── Stage A inputs from settled bars ──────────────────────────────────────

def technicals(df: pd.DataFrame) -> dict:
    """200-DMA, Ichimoku cloud, RSI14, Bollinger %B, 10-day return, 30-day
    CLOSING low (§4.2) and HV30 — all on settled bars."""
    from .indicators import bollinger, rsi
    c = df["close"].astype(float)
    h = df["high"].astype(float) if "high" in df else c
    lo = df["low"].astype(float) if "low" in df else c
    last = float(c.iloc[-1])
    out = {"close": round(last, 2), "as_of": str(df.index[-1])[:10], "sessions": int(len(c))}
    if len(c) < 200:
        out["error"] = f"only {len(c)} sessions held; the 200-DMA needs 200"
        return out
    sma200 = float(c.tail(200).mean())
    # The cloud comes from the ONE Ichimoku definition (the equity trader's);
    # a second copy stops matching the day the periods change.
    from .paper.strategies._equity_trader_signal import compute_indicators
    ich = compute_indicators(pd.DataFrame({"High": h, "Low": lo, "Close": c}))
    cloud_top = float(ich["cloud_top"].iloc[-1])
    bb = bollinger(c, 20, 2.0).iloc[-1]
    pct_b = float(bb["percent_b"]) if bb["percent_b"] == bb["percent_b"] else None
    lr = (c / c.shift(1)).apply(math.log).dropna()
    out.update({
        "sma200": round(sma200, 2), "above_200dma": bool(last > sma200),
        "cloud_top": round(cloud_top, 2), "above_cloud": bool(last > cloud_top),
        "rsi14": round(float(rsi(c, 14).iloc[-1]), 1),
        "bb_pct_b": round(pct_b, 2) if pct_b is not None else None,
        "ret_10d": round(float(last / float(c.iloc[-11]) - 1), 4) if len(c) > 11 else None,
        "swing_low_30d": round(float(c.tail(30).min()), 2),
        "hv_30d": round(float(lr.tail(30).std() * math.sqrt(252)), 4) if len(lr) >= 20 else None,
    })
    return out


def stage_a(sym: str, bucket: str, tech: dict, next_earnings: str | None, earnings_basis: str,
            expiry: str, held: set[str], sector: str, sector_open_gbp: float, total_open_gbp: float,
            s: dict) -> tuple[list[str], list[str]]:
    """§4.1 — (hard rejections, warnings). Empty rejections = eligible. A gate
    that cannot be computed is a rejection too, never a pass."""
    hard, warn = [], []
    if tech.get("error"):
        return [f"no_bars: {tech['error']}"], warn
    if bucket == "single":
        if next_earnings is None:
            hard.append(f"earnings_unknown: {earnings_basis}")
        elif next_earnings <= expiry:
            hard.append(f"earnings_inside_expiry: reports {next_earnings}, expiry {expiry} ({earnings_basis})")
    if s["require_above_200dma"] and not tech["above_200dma"]:
        hard.append(f"below_200dma: close {tech['close']} < {tech['sma200']}")
    if s["require_above_ichimoku_cloud"] and not tech["above_cloud"]:
        (warn if bucket == "index" else hard).append(f"below_cloud: close {tech['close']} < cloud {tech['cloud_top']}")
    if tech.get("ret_10d") is not None and tech["ret_10d"] < float(s["falling_knife_10d_return"]) and not tech["above_200dma"]:
        hard.append(f"falling_knife: 10-day {tech['ret_10d']:+.1%} and below the 200-DMA")
    if s["avoid_names_already_held"] and sym in held:
        hard.append("already_held")
    if sector_open_gbp >= float(s["max_loss_per_sector_gbp"]):
        hard.append(f"sector_cap: {sector} already carries £{sector_open_gbp:,.0f}")
    if total_open_gbp >= float(s["phase_1_total_max_loss_gbp"]):
        hard.append(f"total_cap: £{total_open_gbp:,.0f} open against £{s['phase_1_total_max_loss_gbp']:,.0f}")
    return hard, warn


# ── Stage B ───────────────────────────────────────────────────────────────

def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def pick_expiry(expiries: list[str], today: date, s: dict) -> tuple[str | None, str]:
    """§4.2 — the MONTHLY (third-Friday) expiry with DTE in [dte_min, dte_max];
    two qualify → the nearer. Expiries are 'YYYY-MM-DD' or 'YYYYMMDD'."""
    cands = []
    for e in expiries:
        e = str(e)
        d = date.fromisoformat(e) if "-" in e else date(int(e[:4]), int(e[4:6]), int(e[6:8]))
        dte = (d - today).days
        if int(s["dte_min"]) <= dte <= int(s["dte_max"]) and d == third_friday(d.year, d.month):
            cands.append((dte, d.isoformat()))
    if not cands:
        return None, f"no monthly expiry {s['dte_min']}–{s['dte_max']} days out (listed: {', '.join(map(str, expiries[:8]))})"
    cands.sort()
    return cands[0][1], f"monthly, {cands[0][0]} DTE"


def pick_strikes(puts: list[dict], swing_low: float, s: dict) -> tuple[dict | None, str]:
    """§4.2 — short = highest listed strike ≤ swing low with |delta| in range;
    long = short − width (nearest listed), trying each width in order."""
    lo_d, hi_d = (float(x) for x in s["short_put_delta_range"])
    by_k = {float(p["strike"]): p for p in puts if p.get("strike") is not None}
    if not by_k:
        return None, "no_strikes"
    below = sorted((k for k in by_k if k <= swing_low), reverse=True)
    if not below:
        return None, f"no strike at or below the 30-day closing low {swing_low}"
    tried = []
    for k in below:
        d = by_k[k].get("delta")
        if d is None:
            tried.append(f"{k:g} (no delta)")
            continue
        if not (lo_d <= abs(float(d)) <= hi_d):
            tried.append(f"{k:g} (Δ {abs(float(d)):.2f})")
            continue
        for w in s["spread_width_preference"]:
            target = k - float(w)
            lower = [x for x in by_k if x < k]
            if not lower:
                continue
            long_k = min(lower, key=lambda x: abs(x - target))
            if abs(long_k - target) > float(w) * 0.5:
                continue
            return ({"short": by_k[k], "long": by_k[long_k], "short_strike": k, "long_strike": long_k,
                     "width": round(k - long_k, 2)},
                    f"short {k:g} Δ {abs(float(d)):.2f}, width {k - long_k:g}")
        tried.append(f"{k:g} (no long leg at a configured width)")
    return None, (f"no strike satisfies delta [{lo_d:.2f}, {hi_d:.2f}] at or below {swing_low} — tried "
                  + ", ".join(tried[:6]))


# ── Stage C ───────────────────────────────────────────────────────────────

def price_spread(short: dict, long: dict, s: dict) -> tuple[dict | None, str]:
    """§4.3 — mids, natural, leg liquidity, OI, credit/width. Quotes must be
    present (stale ones are rejected BEFORE this by the fetcher)."""
    for name, leg in (("short", short), ("long", long)):
        if leg.get("bid") in (None, 0) or leg.get("ask") in (None, 0):
            return None, f"stale_quote: {name} leg has no bid/ask"
    mid_s = (float(short["bid"]) + float(short["ask"])) / 2
    mid_l = (float(long["bid"]) + float(long["ask"])) / 2
    sp_s = (float(short["ask"]) - float(short["bid"])) / mid_s
    sp_l = (float(long["ask"]) - float(long["bid"])) / mid_l
    width = float(short["strike"]) - float(long["strike"])
    out = {"short_bid": float(short["bid"]), "short_ask": float(short["ask"]),
           "long_bid": float(long["bid"]), "long_ask": float(long["ask"]),
           "credit_mid": round(mid_s - mid_l, 2), "credit_natural": round(float(short["bid"]) - float(long["ask"]), 2),
           "leg_spread_pct": {"short": round(sp_s, 3), "long": round(sp_l, 3)},
           "short_oi": short.get("open_interest", short.get("openInterest")),
           "liquidity_score": round(max(0.0, 1 - (sp_s + sp_l) / 2), 3)}
    out["credit_to_width"] = round(out["credit_mid"] / width, 4) if width else None
    mx = float(s["max_leg_bid_ask_pct"])
    # §4.3 says EACH leg ≤ 15%, but the §10 golden long leg (2.20/2.75) is 22%
    # wide — a far-OTM long leg usually is, in percent. The acceptance test
    # wins: the SHORT leg (the one the market fills against) is the hard gate;
    # a wide long leg is a warning and lowers the liquidity score. Owner to confirm.
    out["warnings"] = [f"wide_long_leg: {sp_l:.0%} > {mx:.0%}"] if sp_l > mx else []
    if sp_s > mx:
        return out, f"illiquid_leg: short leg spread {sp_s:.0%} > {mx:.0%}"
    oi = out["short_oi"]
    if oi is None:
        # Unknown is not low. The live chain route served no open interest
        # for KO on the first scheduled scan (24 Nov 2026) and the row read
        # "low_oi: None < 100" — a data gap reported as a failed gate.
        return out, "oi_unavailable: the chain served no open interest for the short leg (gate not computable)"
    if int(oi) < int(s["min_open_interest_short_leg"]):
        return out, f"low_oi: short leg OI {oi} < {s['min_open_interest_short_leg']}"
    if out["credit_mid"] <= 0:
        return out, "no_credit"
    if out["credit_to_width"] < float(s["min_credit_to_width"]):
        return out, f"thin_credit: {out['credit_to_width']:.1%} of width < {float(s['min_credit_to_width']):.0%}"
    return out, "priced"


# ── Stage E ───────────────────────────────────────────────────────────────

def size(width: float, credit: float, fx_gbp_per_usd: float, sector_open_gbp: float,
         total_open_gbp: float, s: dict) -> dict:
    """§4.5 — contracts = floor(cap_usd / max_loss_per_spread_usd), cap = the
    tightest of per-trade, sector room, total room."""
    per_spread = (width - credit) * 100
    rooms = {"per_trade": float(s["max_loss_per_trade_gbp"]),
             "sector": float(s["max_loss_per_sector_gbp"]) - sector_open_gbp,
             "total": float(s["phase_1_total_max_loss_gbp"]) - total_open_gbp}
    binding = min(rooms, key=rooms.get)
    cap_usd = max(0.0, rooms[binding]) / fx_gbp_per_usd
    n = int(math.floor(cap_usd / per_spread)) if per_spread > 0 else 0
    return {"contracts": n, "max_loss_per_spread_usd": round(per_spread, 2),
            "max_loss_usd": round(n * per_spread, 2), "max_loss_gbp": round(n * per_spread * fx_gbp_per_usd, 2),
            "income_usd": round(credit * 100 * n, 2), "binding_cap": binding,
            "room_gbp": round(rooms[binding], 2), "fx_gbp_per_usd": round(fx_gbp_per_usd, 4)}


# ── the cash-secured alternative (same short strike, no long leg) ─────────

def cash_secured_put(short: dict, spot: float, dte: int, iv: float | None, hv: float | None,
                     fx_gbp_per_usd: float, csp_open_notional_gbp: float, s: dict) -> dict:
    """The same short put sold cash-secured. Credit = the short leg's mid (no
    long leg to pay for); risk = the strike to zero, so it is sized by CASH:
    contracts = floor(min(per-name notional room, total notional room) ÷
    (strike × 100)). Shown beside the spread for the owner to choose."""
    k = float(short["strike"])
    bid, ask = short.get("bid"), short.get("ask")
    if bid in (None, 0) or ask in (None, 0):
        return {"eligible": False, "why": "stale_quote"}
    mid = (float(bid) + float(ask)) / 2
    per_name = float(s["csp_max_notional_per_name_gbp"]) / fx_gbp_per_usd
    total_room = max(0.0, float(s["csp_total_notional_gbp"]) - csp_open_notional_gbp) / fx_gbp_per_usd
    room_usd = min(per_name, total_room)
    n = int(math.floor(room_usd / (k * 100)))
    p = probabilities(spot, k, k, mid, dte, iv) if iv else {}
    out = {"structure": "cash_secured_put", "short_strike": k, "credit_mid": round(mid, 2),
           "credit_natural": float(bid), "breakeven": round(k - mid, 2),
           "notional_per_contract_usd": round(k * 100, 2), "contracts": n,
           "cash_required_usd": round(n * k * 100, 2), "cash_required_gbp": round(n * k * 100 * fx_gbp_per_usd, 2),
           "income_usd": round(mid * 100 * n, 2),
           "income_pct_of_cash": round(mid / k * 100, 2),
           "annualised_pct_of_cash": round(mid / k * 365 / max(dte, 1) * 100, 1),
           "max_loss_usd": round(n * (k - mid) * 100, 2),
           "p_full_profit_iv": p.get("p_full_profit"), "p_assignment_iv": p.get("p_loss_any"),
           "binding": "per_name" if per_name <= total_room else "total_notional",
           "room_gbp": round(room_usd * fx_gbp_per_usd, 2)}
    if n == 0:
        out.update(eligible=False, why=f"{out['binding']}_cap: £{out['room_gbp']:,.0f} buys no contract at {k:g}")
    elif p.get("p_full_profit") is not None and p["p_full_profit"] < float(s["csp_min_pop_full_credit"]):
        out.update(eligible=False, why=f"low_pop: {p['p_full_profit']:.0%} < {float(s['csp_min_pop_full_credit']):.0%} (CSP floor)")
    else:
        out.update(eligible=True, why="cash permits",
                   instruction_hint=f"SELL {n} × {short.get('symbol', '')} {k:g}P cash-secured, LMT {mid:.2f} (cash {n * k * 100:,.0f} $)")
    return out


# ── the whole evaluation of ONE underlying from plain inputs ─────────────

def evaluate(sym: str, *, bucket: str, sector: str, spot: float, tech: dict, expiries: list[str],
             puts_for, next_earnings: str | None, earnings_basis: str, held: set[str],
             sector_open_gbp: float, total_open_gbp: float, fx_gbp_per_usd: float, s: dict,
             today: date, csp_open_notional_gbp: float = 0.0) -> dict:
    """Stages A–F for one name. `puts_for(expiry) -> list[dict]` returns the
    put legs (strike, bid, ask, delta, iv, open_interest) for that expiry —
    live from the broker in production, the §10 table in the golden replay."""
    row = {"symbol": sym, "sector": sector, "bucket": bucket, "spot": spot, "gates": {}, "rejected": [], "warnings": []}
    expiry, why_e = pick_expiry(expiries, today, s)
    row["expiry"], row["expiry_note"] = expiry, why_e
    if expiry is None:
        row["rejected"].append(f"no_expiry: {why_e}"); return row
    row["dte"] = (date.fromisoformat(expiry) - today).days
    row["gates"] = {"earnings_ok": (next_earnings is not None and next_earnings > expiry) if bucket == "single" else None,
                    "next_earnings": next_earnings, "earnings_basis": earnings_basis,
                    "above_200dma": tech.get("above_200dma"), "sma200": tech.get("sma200"),
                    "above_cloud": tech.get("above_cloud"), "cloud_top": tech.get("cloud_top"),
                    "swing_low_30d": tech.get("swing_low_30d"), "rsi14": tech.get("rsi14"), "bb_pct_b": tech.get("bb_pct_b"),
                    "ret_10d": tech.get("ret_10d"), "hv_30d": tech.get("hv_30d"), "bars_as_of": tech.get("as_of")}
    hard, warn = stage_a(sym, bucket, tech, next_earnings, earnings_basis, expiry, held, sector,
                         sector_open_gbp, total_open_gbp, s)
    row["warnings"] = warn
    if hard:
        row["rejected"] = hard; return row
    puts = puts_for(expiry)
    if not puts:
        row["rejected"].append("stale_quote: no quoted put legs for the expiry"); return row
    legs, why_k = pick_strikes(puts, float(tech["swing_low_30d"]), s)
    row["strike_note"] = why_k
    if legs is None:
        row["rejected"].append(f"no_strike: {why_k}"); return row
    legs["short"] = dict(legs["short"], symbol=sym)
    row.update({k: legs[k] for k in ("short_strike", "long_strike", "width")})
    px, why_p = price_spread(legs["short"], legs["long"], s)
    if px:
        row["warnings"] = row.get("warnings", []) + px.pop("warnings", [])
        row.update(px)
    if why_p != "priced":
        row["rejected"].append(why_p); return row
    iv = legs["short"].get("iv")
    iv = float(iv) if iv else None
    if iv and iv > 3:
        iv = iv / 100.0  # a percent field
    hv = tech.get("hv_30d")
    row["iv_short"], row["hv_30d"] = iv, hv
    row["iv_hv_ratio"] = round(iv / hv, 2) if iv and hv else None
    if not iv:
        row["rejected"].append("no_iv: short leg carries no implied volatility"); return row
    p_iv = probabilities(spot, legs["short_strike"], legs["long_strike"], px["credit_mid"], row["dte"], iv)
    p_hv = probabilities(spot, legs["short_strike"], legs["long_strike"], px["credit_mid"], row["dte"], hv) if hv else {}
    row.update({"breakeven": p_iv["breakeven"], "cushion_pct": round((spot - legs["short_strike"]) / spot, 4),
                "p_full_profit_iv": p_iv["p_full_profit"], "p_loss_any_iv": p_iv["p_loss_any"], "p_max_loss_iv": p_iv["p_max_loss"],
                "one_sd_move_pct": p_iv["one_sd_move_pct"],
                "p_full_profit_hv": p_hv.get("p_full_profit"), "p_max_loss_hv": p_hv.get("p_max_loss")})
    if p_iv["p_full_profit"] < float(s["min_pop_full_credit"]):
        row["rejected"].append(f"low_pop: P(full profit) {p_iv['p_full_profit']:.0%} < {float(s['min_pop_full_credit']):.0%}"); return row
    sz = size(legs["width"], px["credit_mid"], fx_gbp_per_usd, sector_open_gbp, total_open_gbp, s)
    row.update(sz)
    if sz["contracts"] == 0:
        row["rejected"].append(f"{sz['binding_cap']}_cap: room £{sz['room_gbp']:,.0f} buys no spread"); return row
    row["score"] = score(p_iv["p_full_profit"], px["credit_to_width"], row["iv_hv_ratio"], row["cushion_pct"],
                         p_iv["one_sd_move_pct"], px["liquidity_score"], s["score_weights"])
    if s.get("csp_enabled"):
        row["csp"] = cash_secured_put(legs["short"], spot, row["dte"], iv, hv, fx_gbp_per_usd,
                                      csp_open_notional_gbp, s)
    row["id"] = f"{sym}-{expiry.replace('-', '')}-{legs['short_strike']:g}-{legs['long_strike']:g}"
    row["exit_rule"] = f"close next session if {sym} closes < {tech['swing_low_30d']}"
    row["take_profit_at"] = round(px["credit_mid"] * float(s["take_profit_pct_of_credit"]), 2)
    row["instruction_hint"] = (f"SELL {sz['contracts']} × {sym} {expiry} {legs['short_strike']:g}P / {legs['long_strike']:g}P combo, "
                               f"LMT {px['credit_mid']:.2f} credit (walk toward max({px['credit_natural']:.2f}, {0.85 * px['credit_mid']:.2f}))")
    return row


# ── live data adapters ────────────────────────────────────────────────────

def _bars(sym: str, sessions: int = 320) -> pd.DataFrame | None:
    from .daily_bars import load_bars
    df = load_bars(sym, "1d", lookback_days=int(sessions * 1.5), fetched_by=STRATEGY)
    return None if df is None or len(df) < 60 else df


def _earnings(base: str, token: str, sym: str, expiry: str | None = None, s: dict | None = None) -> tuple[str | None, str]:
    """Next report date. Three honest answers:
    1. a SCHEDULED date in the store → that date;
    2. none scheduled, but the store's forward window (its last harvest +
       `earnings_store_window_days`) reaches past the expiry → no report
       inside the expiry, basis says so;
    3. otherwise ask Finnhub's calendar live when a key exists; else unknown
       (a rejection, never a pass)."""
    import requests
    s = s or DEFAULTS
    h = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.get(f"{base.rstrip('/')}/api/earnings-calendar/{sym}", headers=h, timeout=30)
        j = r.json() if r.status_code == 200 else {}
    except Exception as exc:  # noqa: BLE001
        return None, f"earnings store unreachable: {exc}"[:120]
    ev = j.get("events") or []
    dates = sorted({str(e.get("report_date") or "")[:10] for e in ev if e.get("report_date")})
    today = date.today().isoformat()
    future = [d for d in dates if d >= today]
    if future:
        return future[0], "scheduled (earnings store)"
    last_up = str((j.get("store") or {}).get("lastUploadUtc") or "")[:10]
    if last_up and expiry:
        horizon = (date.fromisoformat(last_up) + timedelta(days=int(s["earnings_store_window_days"]))).isoformat()
        if horizon >= expiry:
            return horizon, f"no report inside the store's window to {horizon} (harvested {last_up})"
    try:
        from .catalysts_finnhub import _get_api_key, fetch_upcoming_earnings
        key = _get_api_key()
        if key and expiry:
            ahead = (date.fromisoformat(expiry) - date.today()).days + 10
            rows = fetch_upcoming_earnings(sym, api_key=key, days_ahead=max(ahead, 30))
            ds = sorted(str(getattr(c, "occurs_on", None) or getattr(c, "date", None) or "")[:10] for c in rows)
            ds = [d for d in ds if d and d >= today]
            if ds:
                return ds[0], "scheduled (Finnhub calendar, live)"
            return (date.today() + timedelta(days=max(ahead, 30))).isoformat(), f"no report inside the next {max(ahead, 30)} days (Finnhub calendar, live)"
    except Exception as exc:  # noqa: BLE001
        return None, f"Finnhub calendar unavailable: {exc}"[:120]
    past = [d for d in dates if d < today]
    return None, (f"no scheduled date held; last report {past[-1]}; store window ends before the expiry and no Finnhub key"
                  if past else "no report date held for this name and no Finnhub key")


def _live_chain(base: str, token: str, sym: str, expiry: str, s: dict) -> tuple[list[dict], str]:
    """Live put legs for one expiry through the API's IBKR chain route. The
    quote cache is often cold on the first call (the known 'stream never
    warms' fault): warm up ×3, then report stale."""
    import time

    import requests
    h = {"Authorization": f"Bearer {token}"} if token else {}
    d = date.fromisoformat(expiry)
    month = d.strftime("%b%y").upper()
    legs, err = [], ""
    for _attempt in range(3):
        try:
            r = requests.get(f"{base.rstrip('/')}/api/ibkr/chain/{sym}",
                             params={"month": month, "right": "P", "maxStrikes": 40, "expiry": d.strftime("%Y%m%d")},
                             headers=h, timeout=120)
            j = r.json() if r.status_code == 200 else {}
            legs, err = j.get("legs") or [], str(j.get("error") or "")
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:120]
        if any(l.get("bid") is not None and l.get("ask") is not None for l in legs):
            break
        time.sleep(5)
    out = []
    for l in legs:
        iv = l.get("iv", l.get("impliedVolPct"))
        out.append({"strike": l.get("strike"), "bid": l.get("bid"), "ask": l.get("ask"), "delta": l.get("delta"),
                    "iv": (float(iv) / 100 if iv and float(iv) > 3 else iv),
                    "open_interest": l.get("openInterest", l.get("open_interest")), "conid": l.get("conId")})
    quoted = [l for l in out if l["bid"] is not None and l["ask"] is not None]
    return quoted, (err or ("live" if quoted else "cold quotes"))


def _expiries(base: str, token: str, sym: str) -> list[str]:
    """Monthly expiries (third Fridays) of the months the chain route lists."""
    import requests
    h = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.get(f"{base.rstrip('/')}/api/ibkr/chain/{sym}/months", headers=h, timeout=60)
        months = (r.json().get("months") or []) if r.status_code == 200 else []
    except Exception:  # noqa: BLE001
        months = []
    out = []
    for m in months:
        try:
            d = datetime.strptime(str(m), "%b%y")
            out.append(third_friday(d.year, d.month).isoformat())
        except ValueError:
            continue
    return out


def _fx_gbp_per_usd(base: str, token: str, s: dict) -> tuple[float, str]:
    """GBP per USD. §2 says live from IBKR balances; no route serves the pair
    yet, so the fallback is used and LABELLED on the artifact."""
    return float(s["fx_gbp_per_usd_fallback"]), "fallback (no live IBKR GBP/USD route yet)"


def held_names(base: str, token: str, rec: dict) -> set[str]:
    """Names with existing stock or option exposure in the LIVE account: Flex
    positions, the live-book artifact and the ledger's open trades."""
    import requests
    h = {"Authorization": f"Bearer {token}"} if token else {}
    held = {str(t.get("symbol")).upper() for t in rec.get("trades") or [] if t.get("status") == "open"}
    try:
        r = requests.get(f"{base.rstrip('/')}/api/integrations/ibkr/flex/positions", headers=h, timeout=60)
        for p in (r.json().get("positions") or []) if r.status_code == 200 else []:
            held.add(str(p.get("symbol") or "").upper())
    except Exception as exc:  # noqa: BLE001
        log.warning("Flex positions unreadable for already_held: %s", exc)
    try:
        r = requests.get(f"{base.rstrip('/')}/api/today-setups/live-book-risk/latest", headers=h, timeout=30)
        art = (r.json().get("artifact") or {}) if r.status_code == 200 else {}
        for key in ("positions", "equity", "calls"):
            for p in art.get(key) or []:
                held.add(str(p.get("symbol") or "").upper())
    except Exception as exc:  # noqa: BLE001
        log.warning("live book unreadable for already_held: %s", exc)
    held.discard("")
    return held


def open_csp_notional_gbp(rec: dict, fx_gbp_per_usd: float) -> float:
    return sum(float(t.get("cash_required_usd") or 0) * fx_gbp_per_usd for t in rec.get("trades") or []
               if t.get("status") == "open" and t.get("structure") == "cash_secured_put")


def open_risk_gbp(rec: dict, fx_gbp_per_usd: float) -> tuple[dict[str, float], float]:
    per_sector: dict[str, float] = {}
    total = 0.0
    for t in rec.get("trades") or []:
        if t.get("status") != "open" or t.get("role") == "hedge":
            continue
        loss = float(t.get("max_loss_usd") or 0) * fx_gbp_per_usd
        per_sector[t.get("sector") or "?"] = per_sector.get(t.get("sector") or "?", 0.0) + loss
        total += loss
    return per_sector, total


# ── Stage F: the scan ─────────────────────────────────────────────────────

def rank(rows: list[dict], s: dict) -> list[dict]:
    """Top candidates by score, one per sector when configured; the rest are
    marked rejected with the reason."""
    cands = sorted((r for r in rows if not r.get("rejected")), key=lambda r: -r.get("score", 0))
    if s["no_two_names_same_sector"]:
        seen, picked = set(), []
        for r in cands:
            if r["sector"] in seen:
                r["rejected"] = ["same_sector_as_a_higher_ranked_candidate"]; continue
            seen.add(r["sector"]); picked.append(r)
        cands = picked
    for r in cands[int(s["max_candidates"]):]:
        r["rejected"] = ["ranked_below_top_candidates"]
    return cands[: int(s["max_candidates"])]


def scan(base: str, token: str, *, only: set[str] | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    s = settings(base, token)
    rec = record(base, token)
    fx, fx_src = _fx_gbp_per_usd(base, token, s)
    held = held_names(base, token, rec)
    per_sector, total = open_risk_gbp(rec, fx)
    csp_open = open_csp_notional_gbp(rec, fx)
    names = [(x, "single", s["sectors"].get(x, "?")) for x in s["single_name_seed"]]
    if int(s.get("phase", 1)) >= 2:
        names += [(x, "index", "Index") for x in s["index_symbols"]]
    rows = []
    for sym, bucket, sector in names:
        if only and sym not in only:
            continue
        try:
            df = _bars(sym)
            tech = technicals(df) if df is not None else {"error": "no settled daily bars held"}
            spot = float(tech.get("close") or 0)
            exps = _expiries(base, token, sym)
            exp_guess, _ = pick_expiry(exps, today, s)
            nxt, basis = _earnings(base, token, sym, exp_guess, s) if bucket == "single" else (None, "index")
            quote_notes: dict = {}

            def puts_for(expiry, _sym=sym, _notes=quote_notes):
                legs, note = _live_chain(base, token, _sym, expiry, s)
                _notes[expiry] = note
                return legs

            row = evaluate(sym, bucket=bucket, sector=sector, spot=spot, tech=tech, expiries=exps,
                           puts_for=puts_for, next_earnings=nxt, earnings_basis=basis, held=held,
                           sector_open_gbp=per_sector.get(sector, 0.0), total_open_gbp=total,
                           fx_gbp_per_usd=fx, s=s, today=today, csp_open_notional_gbp=csp_open)
            row["quote_source"] = quote_notes
            row["spot_basis"] = f"settled close {tech.get('as_of')}"
        except Exception as exc:  # noqa: BLE001 — one name's failure is named, never hidden
            row = {"symbol": sym, "sector": sector, "bucket": bucket, "rejected": [f"error: {exc}"[:200]]}
        rows.append(row)
    top = rank(rows, s)
    return {"strategy": STRATEGY, "spec": SPEC, "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "settings": s, "fx": {"gbp_per_usd": fx, "source": fx_src},
            "caps": {"open_total_gbp": round(total, 2), "open_by_sector_gbp": {k: round(v, 2) for k, v in per_sector.items()},
                     "total_cap_gbp": s["phase_1_total_max_loss_gbp"], "sector_cap_gbp": s["max_loss_per_sector_gbp"],
                     "csp_open_notional_gbp": round(csp_open, 2), "csp_total_notional_gbp": s["csp_total_notional_gbp"]},
            "candidates": top,
            "rejected": [{"symbol": r["symbol"], "reasons": r["rejected"], "gates": r.get("gates"), "warnings": r.get("warnings"),
                          "expiry": r.get("expiry"), "short_strike": r.get("short_strike"), "long_strike": r.get("long_strike"),
                          "credit_mid": r.get("credit_mid"), "p_full_profit_iv": r.get("p_full_profit_iv"),
                          "quote_source": r.get("quote_source")}
                         for r in rows if r.get("rejected")],
            "held_live": sorted(held), "placed_orders": 0,
            "note": "TradePro creates no order. Stage G (the IBKR instruction) is on approval, by the owner's agent."}


# ── Stage H: monitor + ledger summary ─────────────────────────────────────

def monitor_trade(t: dict, close: float | None, close_as_of: str | None, spread_mid: float | None,
                  debit_natural: float | None, s: dict, today: date) -> dict:
    """§4.8 for ONE open trade from plain inputs (testable). Alerts carry a
    stable `key` so the sender can de-duplicate ("generated once")."""
    row = {"id": t.get("id"), "symbol": t.get("symbol"), "role": t.get("role", "income"), "expiry": t.get("expiry"),
           "short_strike": t.get("short_strike"), "long_strike": t.get("long_strike"), "contracts": t.get("contracts"),
           "credit": t.get("credit_per_spread"), "close": close, "close_as_of": close_as_of, "alerts": []}
    try:
        row["dte"] = (date.fromisoformat(str(t.get("expiry"))) - today).days
    except Exception:  # noqa: BLE001
        row["dte"] = None
    exit_level = t.get("swing_low_at_entry") or (t.get("gates_at_entry") or {}).get("swing_low_30d_close")
    row["exit_level"] = exit_level
    if close is not None and exit_level and close < float(exit_level):
        row["alerts"].append({"kind": "EXIT_NEXT_SESSION", "key": f"{t.get('id')}:exit:{close_as_of}",
                              "detail": f"{t.get('symbol')} closed {close:.2f} < {exit_level} on {close_as_of}"
                                        + (f"; debit to close ≈ {debit_natural:.2f}" if debit_natural is not None else "")})
    if close is not None and row["dte"] is not None and row["dte"] <= 7 and t.get("short_strike") \
            and abs(close / float(t["short_strike"]) - 1) <= 0.02:
        row["alerts"].append({"kind": "GAMMA_RISK", "key": f"{t.get('id')}:gamma:{close_as_of}",
                              "detail": f"{row['dte']} DTE and the short strike is within 2% of spot"})
    credit = float(t.get("credit_per_spread") or 0)
    if spread_mid is not None:
        row["spread_mid"] = round(spread_mid, 2)
        row["debit_to_close_natural"] = debit_natural
        if credit:
            row["mark_pct_of_credit"] = round(spread_mid / credit, 3)
            tp = credit * float(s["take_profit_pct_of_credit"])
            if spread_mid <= tp:
                row["alerts"].append({"kind": "TAKE_PROFIT", "key": f"{t.get('id')}:tp",
                                      "detail": f"spread mid {spread_mid:.2f} ≤ {tp:.2f}; buy back at ≈ {spread_mid:.2f}"})
    return row


def monitor(rec: dict, base: str, token: str, s: dict, *, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for t in rec.get("trades") or []:
        if t.get("status") != "open":
            continue
        df = _bars(str(t.get("symbol")), 120)
        close = float(df["close"].iloc[-1]) if df is not None else None
        close_as_of = str(df.index[-1])[:10] if df is not None else None
        mid = natural = None
        note = None
        if t.get("role", "income") == "income" and t.get("expiry") and t.get("short_strike") and t.get("long_strike"):
            legs, qnote = _live_chain(base, token, str(t["symbol"]), str(t["expiry"]), s)
            by_k = {float(l["strike"]): l for l in legs if l.get("strike") is not None}
            sh, lg = by_k.get(float(t["short_strike"])), by_k.get(float(t["long_strike"]))
            if sh and lg:
                mid = (float(sh["bid"]) + float(sh["ask"])) / 2 - (float(lg["bid"]) + float(lg["ask"])) / 2
                natural = round(float(sh["ask"]) - float(lg["bid"]), 2)
            else:
                note = f"spread not priced ({qnote})"
        row = monitor_trade(t, close, close_as_of, mid, natural, s, today)
        if note:
            row["mark_note"] = note
        if df is None:
            row["note"] = "no settled bars for the exit check"
        out.append(row)
    return out


def record_summary(rec: dict, s: dict, fx_gbp_per_usd: float | None = None) -> dict:
    fx = fx_gbp_per_usd or float(s["fx_gbp_per_usd_fallback"])
    trades = rec.get("trades") or []
    income = [t for t in trades if t.get("role", "income") == "income"]
    closed = [t for t in income if t.get("status") == "closed"]
    open_ = [t for t in income if t.get("status") == "open"]
    realised = sum(float(t.get("realised_usd") or 0) for t in closed)
    wins = [t for t in closed if float(t.get("realised_usd") or 0) > 0]
    out = {"n_closed": len(closed), "n_open": len(open_),
           "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
           "realised_usd": round(realised, 2),
           "open_credit_usd": round(sum(float(t.get("credit_usd") or 0) for t in open_), 2),
           "open_max_loss_usd": round(sum(float(t.get("max_loss_usd") or 0) for t in open_), 2),
           "open_max_loss_gbp": round(sum(float(t.get("max_loss_usd") or 0) for t in open_) * fx, 2),
           "capital_gbp": s["capital_gbp"], "target_pct_per_year": round(float(s["target_annual_return"]) * 100, 1),
           "target_gbp_per_month": round(float(s["capital_gbp"]) * float(s["target_annual_return"]) / 12, 0)}
    opened = sorted(str(t.get("opened")) for t in income if t.get("opened"))
    out["annualised_pct_on_capital"] = None
    if opened and closed:
        days = max(1, (date.today() - date.fromisoformat(opened[0])).days)
        out["income_ytd_gbp"] = round(realised * fx, 2)
        out["annualised_pct_on_capital"] = round(realised * fx / float(s["capital_gbp"]) * 365 / days * 100, 2)
    out["verdict"] = ("too few (needs 30 closed)" if len(closed) < 30 else
                      ("PASS" if (out.get("annualised_pct_on_capital") or 0) >= out["target_pct_per_year"] else "FAIL"))
    return out
