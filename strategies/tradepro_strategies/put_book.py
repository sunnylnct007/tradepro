"""Put-book manager — the owner's assignment-or-repair policy as code
(options review item 17, 28 Nov 2026). Pure: rows in, classes out.

Every short put is classified daily by FIXED rules; the only human inputs are
the per-symbol conviction flag (settings-kv `risk_guardrails.want_to_own`:
want / neutral / no_more…) and the cash reserve. Builds on the live-risk rows
(`cli/live_risk.py`: strike, credit = Flex cost basis, mark, spot, dte,
structure, events) and `quant_engine.options.manage.assess` (effective basis,
close vs hold P&L). It never contradicts a CLOSE from the pre-existing rules —
those stay on the row as `verdict`; this adds the owner's `policy`.

    effective cost = strike − net premium received
    gap            = (spot − effective cost) / effective cost

Classification, first match wins (§17 of the review):
  1. event first   — earnings inside the expiry and expiry ≤ 30 days → CLOSE
                     before the event
  2. don't-want    — flag says no more of this name → REPAIR
  3. gap ≥ 0       — HOLD (decay is on our side)
  4. gap 0…−10%, want-to-own, cash test passes, single-name cap holds after
                     assignment → HOLD / ACCEPT ASSIGNMENT
  5. otherwise     — REPAIR (gap below −10%, or a cap breach after assignment)
A defined-risk spread's short leg is HOLD (managed by the income monitor) and
a hedge's financing leg is not a put-book row at all.

Repair means close INTO A BOUNCE, not on a down day: "cheap exit now" is raised
when the buy-back is within 25% of the premium received, the underlying is
≥ 8% off its 10-day low, or it is back at its 20-day average; never on a day
the sector ETF is down ≥ 3% (the caller passes `sector_down_pct`).

Cash calendar: assignment cash (strike × 100 × contracts for naked puts, the
width for spreads — the row's `obligation_usd`) grouped by expiry month
against `cash_available_usd − cash_reserve_usd`; when a month would exceed
it, that month's puts are ranked by gap and the lowest proposed for closing
ahead of expiry. Cash available is an OWNER INPUT (settings-kv
`risk_guardrails.cash_available_usd`) until the Flex query carries the cash
report; without it the test reads "unknown" and nothing is called ACCEPT.

Nothing here places an order.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Callable, Optional

from .quant_engine.options.manage import parse_occ

CLOSE_BEFORE_EVENT_DTE = 30
ACCEPT_GAP_FLOOR_PCT = -10.0
CHEAP_EXIT_BUYBACK_PCT_OF_CREDIT = 25.0
CHEAP_EXIT_BOUNCE_FROM_10D_LOW_PCT = 8.0
NO_REPAIR_ON_SECTOR_DOWN_PCT = -3.0
NAME_CAP_FRACTION_OF_NAV = 0.10      # mirrors live_risk.PUT_LIMIT_NAME
INDEX_ETFS = ("SPY", "QQQ", "IWM", "SMH", "IGV", "XSP", "XLK", "XLE", "XLF", "DIA")

BarsFn = Callable[[str], Optional[dict]]   # symbol -> {"low_10d":..,"sma20":..,"close":..,"prev_close":..}


def flag_for(symbol: str, want_to_own: dict | None, *, expiry: str | None = None,
             strike: float | None = None) -> str:
    """want | neutral | no_more. A plain string applies to every contract; a
    dict entry carries per-contract exceptions — "no more of this name beyond
    one existing strike" is {"default": "no_more", "accept": ["YYYY-MM-DD:strike"]}:
    that contract is accepted, anything else on the name is not."""
    entry = (want_to_own or {}).get(symbol.upper())
    if isinstance(entry, dict):
        key = f"{expiry}:{strike:g}" if (expiry and strike is not None) else None
        if key and key in [str(x) for x in (entry.get("accept") or [])]:
            return "want"
        if key and key in [str(x) for x in (entry.get("reject") or [])]:
            return "no_more"
        v = str(entry.get("default") or "neutral").lower()
    else:
        v = str(entry or "neutral").lower()
    if v.startswith("want"):
        return "want"
    if v.startswith("no"):
        return "no_more"
    return "neutral"


def effective_cost(strike: float, credit: float | None) -> float | None:
    if strike is None or credit is None:
        return None
    return float(strike) - float(credit)


def classify(row: dict, *, flag: str, cash_ok: Optional[bool], name_cap_ok: bool,
             earnings_inside: bool) -> tuple[str, str]:
    """(policy, reason) for one short put. `cash_ok` None = unknown (no cash
    figure) — then ACCEPT is never granted, by design."""
    structure = str(row.get("structure") or "naked")
    if structure == "credit_spread":
        return "HOLD", "defined-risk spread — max loss is the width; the income monitor manages it (50% take-profit, swing-low exit)"
    cost = effective_cost(row.get("strike"), row.get("credit"))
    spot = row.get("spot")
    if cost is None or spot is None or cost <= 0:
        return "UNKNOWN", "cannot classify — no " + ", ".join(n for n, v in (("strike", row.get("strike")), ("premium", row.get("credit")), ("spot", spot)) if v is None)
    gap = (float(spot) - cost) / cost * 100.0
    dte = row.get("dte")
    if earnings_inside and dte is not None and dte <= CLOSE_BEFORE_EVENT_DTE:
        return "CLOSE", f"earnings inside the expiry with {dte}d left — close before the event (rule 1)"
    if flag == "no_more":
        return "REPAIR", f"flagged no-more-of-this-name; gap {gap:+.1f}% to the {cost:.2f} effective cost (rule 2)"
    if gap >= 0:
        return "HOLD", f"spot {float(spot):.2f} is {gap:+.1f}% above the {cost:.2f} effective cost — decay is on our side (rule 3)"
    if gap >= ACCEPT_GAP_FLOOR_PCT and flag == "want":
        if cash_ok is True and name_cap_ok:
            return "HOLD/ACCEPT", f"gap {gap:+.1f}%, want-to-own, cash covers assignment after the reserve, name cap holds (rule 4)"
        why = []
        if cash_ok is None:
            why.append("cash available unknown (settings-kv risk_guardrails.cash_available_usd)")
        elif cash_ok is False:
            why.append("cash after the reserve does not cover assignment")
        if not name_cap_ok:
            why.append("single-name cap breached after assignment")
        return "HOLD", f"gap {gap:+.1f}%, want-to-own, but " + "; ".join(why) + " — HOLD, not ACCEPT (rule 4 not met)"
    if gap >= ACCEPT_GAP_FLOOR_PCT:
        return "HOLD", f"gap {gap:+.1f}% inside the −10% line, no conviction flag — hold and watch (rule 4, neutral)"
    return "REPAIR", f"gap {gap:+.1f}% is below the −10% line to the {cost:.2f} effective cost (rule 5)"


def cheap_exit(row: dict, tech: dict | None, *, sector_down_pct: float | None) -> dict:
    """The 'close into a bounce' triggers, each with its number; suppressed on
    a sector down day (said, not hidden)."""
    reasons = []
    credit, mark = row.get("credit"), row.get("mark")
    if credit and mark is not None and credit > 0 and mark <= credit * (CHEAP_EXIT_BUYBACK_PCT_OF_CREDIT / 100.0):
        reasons.append(f"buy-back {mark:.2f} is {mark / credit * 100:.0f}% of the {credit:.2f} premium received")
    spot = row.get("spot")
    if tech and spot:
        low10 = tech.get("low_10d")
        if low10 and low10 > 0 and spot / low10 - 1 >= CHEAP_EXIT_BOUNCE_FROM_10D_LOW_PCT / 100.0:
            reasons.append(f"underlying {spot / low10 * 100 - 100:+.1f}% off its 10-day low {low10:.2f}")
        sma20 = tech.get("sma20")
        if sma20 and sma20 > 0 and abs(spot / sma20 - 1) <= 0.01:
            reasons.append(f"back at its 20-day average ({sma20:.2f})")
    suppressed = sector_down_pct is not None and sector_down_pct <= NO_REPAIR_ON_SECTOR_DOWN_PCT
    return {"now": bool(reasons) and not suppressed, "reasons": reasons,
            "suppressed": (f"sector ETF {sector_down_pct:+.1f}% today — no repair close on a down day" if suppressed and reasons else None)}


def cash_calendar(rows: list[dict], *, cash_available_usd: float | None, cash_reserve_usd: float | None,
                  gap_of: dict[str, float]) -> dict:
    """Assignment cash by expiry month against cash minus the reserve."""
    by_month: dict[str, dict] = {}
    for r in rows:
        exp = str(r.get("expiry") or "")[:7]
        if not exp or str(r.get("structure") or "naked") == "hedge_short_leg":
            continue
        ob = float(r.get("obligation_usd") or 0)
        m = by_month.setdefault(exp, {"month": exp, "assignment_usd": 0.0, "puts": []})
        m["assignment_usd"] += ob
        m["puts"].append({"symbol": r.get("symbol"), "strike": r.get("strike"), "qty": r.get("qty"),
                          "obligation_usd": round(ob), "gap_pct": gap_of.get(r.get("contract") or "", None)})
    free = (cash_available_usd - (cash_reserve_usd or 0)) if cash_available_usd is not None else None
    months = []
    for m in sorted(by_month.values(), key=lambda x: x["month"]):
        m["assignment_usd"] = round(m["assignment_usd"])
        m["free_cash_usd"] = round(free) if free is not None else None
        m["short_by_usd"] = round(m["assignment_usd"] - free) if free is not None and m["assignment_usd"] > free else 0
        m["status"] = "unknown (no cash figure)" if free is None else ("SHORT" if m["short_by_usd"] > 0 else "covered")
        if m["short_by_usd"] > 0:
            ranked = sorted(m["puts"], key=lambda p: (p["gap_pct"] if p["gap_pct"] is not None else 0))
            need, close = m["short_by_usd"], []
            for p_ in ranked:
                if need <= 0:
                    break
                close.append(p_["symbol"]); need -= p_["obligation_usd"]
            m["propose_closing_ahead"] = close
        months.append(m)
    return {"cash_available_usd": cash_available_usd, "cash_reserve_usd": cash_reserve_usd,
            "free_cash_usd": round(free) if free is not None else None, "months": months,
            "note": None if free is not None else "cash available is an owner input (settings-kv risk_guardrails.cash_available_usd) until the Flex query carries the cash report"}


def hedge_coverage_gap(positions: list[dict], rows: list[dict]) -> dict:
    """Latest short-put expiry vs latest index-ETF long-put expiry."""
    put_exp = sorted({str(r.get("expiry")) for r in rows if r.get("expiry")})
    hedge_exp = []
    for p in positions:
        occ = parse_occ(str(p.get("symbol") or ""))
        if occ and occ[2] == "P" and float(p.get("quantity") or 0) > 0 and occ[0] in INDEX_ETFS:
            hedge_exp.append(occ[1])
    hedge_exp = sorted(set(hedge_exp))
    last_put, last_hedge = (put_exp[-1] if put_exp else None), (hedge_exp[-1] if hedge_exp else None)
    uncovered = [e for e in put_exp if last_hedge is None or e > last_hedge]
    return {"last_put_expiry": last_put, "last_hedge_expiry": last_hedge, "hedge_expiries": hedge_exp,
            "put_expiries_after_hedges_end": uncovered,
            "note": (f"puts run to {last_put} but the hedges end {last_hedge or 'never — no index-ETF long puts'}"
                     if uncovered else "every short-put expiry sits inside a hedge's life")}


def put_book(rows: list[dict], *, positions: list[dict], kv: dict | None, nav_usd: float | None,
             bars_for: BarsFn | None = None, sector_down_pct_of: Callable[[str], Optional[float]] | None = None,
             today: date | None = None) -> dict:
    kv = kv or {}
    want = kv.get("want_to_own") or {}
    reserve = kv.get("cash_reserve_usd")
    cash = kv.get("cash_available_usd")
    free = (float(cash) - float(reserve or 0)) if cash is not None else None
    by_name_ob: dict[str, float] = defaultdict(float)
    for r in rows:
        by_name_ob[str(r.get("symbol") or "").upper()] += float(r.get("obligation_usd") or 0)
    out_rows, gap_of = [], {}
    for r in rows:
        if str(r.get("structure") or "naked") == "hedge_short_leg":
            continue
        sym = str(r.get("symbol") or "").upper()
        cost = effective_cost(r.get("strike"), r.get("credit"))
        gap = ((float(r["spot"]) - cost) / cost * 100.0) if (cost and r.get("spot")) else None
        gap_of[r.get("contract") or ""] = gap
        ev = r.get("events") or {}
        earnings_inside = any(str(e.get("kind")) == "earnings" for e in (ev.get("events") or [])) if isinstance(ev, dict) else False
        name_cap_ok = True if not nav_usd else by_name_ob[sym] <= NAME_CAP_FRACTION_OF_NAV * float(nav_usd)
        ob = float(r.get("obligation_usd") or 0)
        cash_ok = None if free is None else (ob <= free)
        flag = flag_for(sym, want, expiry=str(r.get("expiry") or "")[:10] or None, strike=r.get("strike"))
        policy, reason = classify(r, flag=flag, cash_ok=cash_ok, name_cap_ok=name_cap_ok,
                                  earnings_inside=earnings_inside)
        tech = None
        if bars_for is not None:
            try:
                tech = bars_for(sym)
            except Exception:  # noqa: BLE001
                tech = None
        sdp = sector_down_pct_of(sym) if sector_down_pct_of else None
        ce = cheap_exit(r, tech, sector_down_pct=sdp) if policy in ("REPAIR", "HOLD", "HOLD/ACCEPT") else {"now": False, "reasons": [], "suppressed": None}
        floor = None
        if policy in ("HOLD", "HOLD/ACCEPT") and tech and tech.get("low_20d") and r.get("spot"):
            support = float(tech["low_20d"])
            floor = {"support": round(support, 2), "below_support": float(r["spot"]) < support,
                     "proposal": (f"buy a put below {support:.2f} NOW — the underlying closed below the measured support"
                                  if float(r["spot"]) < support else
                                  f"buy a put below {support:.2f} on a green day (plain long put, same expiry)")}
        out_rows.append({
            "symbol": sym, "contract": r.get("contract"), "expiry": r.get("expiry"), "strike": r.get("strike"),
            "qty": r.get("qty"), "dte": r.get("dte"), "structure": r.get("structure"),
            "premium_received": r.get("credit"), "mark": r.get("mark"), "spot": r.get("spot"),
            "effective_cost": round(cost, 2) if cost else None, "gap_pct": round(gap, 2) if gap is not None else None,
            "flag": flag, "earnings_inside_expiry": earnings_inside,
            "events": ev.get("names") if isinstance(ev, dict) else None,
            "policy": policy, "policy_reason": reason,
            "verdict": r.get("action"), "verdict_rule": r.get("rule"),
            "cheap_exit": ce, "floor": floor,
            "close_pnl_usd": r.get("close_pnl"), "hold_pnl_usd": r.get("hold_pnl"),
        })
    order = {"CLOSE": 0, "REPAIR": 1, "HOLD/ACCEPT": 2, "HOLD": 3, "UNKNOWN": 4}
    out_rows.sort(key=lambda x: (order.get(x["policy"], 9), x["dte"] if x["dte"] is not None else 9999))
    counts = defaultdict(int)
    for x in out_rows:
        counts[x["policy"]] += 1
    return {
        "version": "PUT_BOOK_V1",
        "as_of": (today or date.today()).isoformat(),
        "counts": dict(counts),
        "cheap_exits_now": [x["symbol"] for x in out_rows if x["cheap_exit"]["now"]],
        "rows": out_rows,
        "cash_calendar": cash_calendar(rows, cash_available_usd=float(cash) if cash is not None else None,
                                       cash_reserve_usd=float(reserve) if reserve is not None else None, gap_of=gap_of),
        "hedge_coverage": hedge_coverage_gap(positions, rows),
        "after_assignment": "a new lot is handed to the covered-call manager: 0.25–0.30 delta, green days, strike above the effective cost where possible",
        "inputs": {"want_to_own_source": "settings-kv risk_guardrails.want_to_own" if want else "none set — every name reads neutral",
                   "cash_available_usd": cash, "cash_reserve_usd": reserve, "nav_usd": nav_usd},
        "placed_orders": 0,
        "how": "effective cost = strike − premium received; gap = spot vs effective cost; rules 1–5 of RISK_GUARDRAILS §12 / review item 17; repairs are proposed into bounces, never on a sector down day",
    }
