"""Macro-event stress test on the live book (options review item 14,
28 Nov 2026). Pure: the exposure block in, net liquidation after each
scenario out, against the owner's floor.

For each segment the P&L of a move m (fraction) is

    net_delta_dollars × m  +  ½ × gamma_dollars_per_1pct × (100 m)²

— first order from the exposure block, second order from the short puts'
gamma (negative for a short-option book, so the loss grows faster than
linearly on the way down). Hedges are inside the segment's net already.
Each segment moves by the scenario's move for the index ETF it is hedged
with (settings-kv `risk_guardrails.segment_hedge_etf`); a segment with no
mapped ETF takes the scenario's "default" move, or the index move times the
segment's beta (`risk_guardrails.segment_beta`, default 1.0).

Scenarios (settings-kv `risk_guardrails.stress_scenarios` overrides):
  base            — the priced outcome: no move
  hawkish_shock   — SMH −7%, IGV −5%, QQQ −4%, SPY −3%, unmapped −2.5%
  dovish_relief   — SMH +3%, IGV +2.5%, QQQ +2%, SPY +1.5%, unmapped +1%

Honesty: a segment with unknown gamma says so; a floor in GBP needs an FX
rate and the output names it; the hike odds are an OWNER INPUT until a feed
exists (settings-kv `macro.fomc_hike_odds_pct`) and the output says
"not available" rather than guessing. Nothing here places an order.
"""
from __future__ import annotations

from datetime import date

DEFAULT_SCENARIOS: dict[str, dict] = {
    # Moves are keyed by the INDEX ETF a segment is hedged with (settings-kv
    # `risk_guardrails.segment_hedge_etf` maps segment → ETF); "default" covers
    # segments with no mapped ETF. Nothing here names a segment of any book.
    "base": {"label": "priced outcome — no move", "index_move_pct": 0.0, "moves": {}},
    "hawkish_shock": {"label": "hawkish surprise", "index_move_pct": -4.0,
                      "moves": {"SMH": -7.0, "SOXX": -7.0, "IGV": -5.0, "QQQ": -4.0, "SPY": -3.0, "default": -2.5}},
    "dovish_relief": {"label": "dovish relief", "index_move_pct": 2.0,
                      "moves": {"SMH": 3.0, "SOXX": 3.0, "IGV": 2.5, "QQQ": 2.0, "SPY": 1.5, "default": 1.0}},
}
DEFAULT_CUSHION_MIN_GBP = 15_000.0


def segment_pnl(seg: dict, move_pct: float) -> dict:
    m = move_pct / 100.0
    net = float(seg.get("net_usd") or 0.0)
    gamma = seg.get("gamma_usd_per_1pct")
    first = net * m
    second = 0.5 * float(gamma) * (move_pct ** 2) if gamma is not None else 0.0
    return {"segment": seg.get("segment"), "move_pct": move_pct, "first_order_usd": round(first, 0),
            "gamma_usd": round(second, 0), "pnl_usd": round(first + second, 0),
            "gamma_unknown_legs": int(seg.get("gamma_unknown_legs") or 0)}


def run_scenario(exposure: dict, scenario: dict, *, betas: dict | None = None,
                 segment_to_etf: dict | None = None) -> dict:
    rows, total = [], 0.0
    moves = scenario.get("moves") or {}
    for seg in exposure.get("segments") or []:
        name = str(seg.get("segment"))
        etf = str((segment_to_etf or {}).get(name) or "").upper()
        if name in moves:                      # an override keyed by segment name (owner's kv)
            mv = float(moves[name])
        elif etf and etf in moves:
            mv = float(moves[etf])
        elif "default" in moves:
            mv = float(moves["default"])
        else:
            mv = float(scenario.get("index_move_pct", 0.0)) * float((betas or {}).get(name, 1.0))
        r = segment_pnl(seg, mv)
        rows.append(r)
        total += r["pnl_usd"]
    return {"label": scenario.get("label"), "index_move_pct": scenario.get("index_move_pct"),
            "segments": rows, "pnl_usd": round(total, 0),
            "gamma_unknown_legs": sum(r["gamma_unknown_legs"] for r in rows)}


def stress(exposure: dict, *, nav_usd: float | None, floor_gbp: float | None, gbp_per_usd: float | None,
           scenarios: dict | None = None, betas: dict | None = None, cushion_min_gbp: float | None = None,
           events: list[dict] | None = None, implied_moves: dict | None = None,
           hike_odds: dict | None = None, today: date | None = None,
           segment_to_etf: dict | None = None) -> dict:
    scen = scenarios or DEFAULT_SCENARIOS
    cushion_min = float(cushion_min_gbp if cushion_min_gbp is not None else DEFAULT_CUSHION_MIN_GBP)
    floor_usd = (float(floor_gbp) / float(gbp_per_usd)) if (floor_gbp and gbp_per_usd) else None
    out_sc = {}
    flags = []
    for key, sc in scen.items():
        r = run_scenario(exposure, sc, betas=betas, segment_to_etf=segment_to_etf)
        nav_after = (float(nav_usd) + r["pnl_usd"]) if nav_usd is not None else None
        r["nav_after_usd"] = round(nav_after, 0) if nav_after is not None else None
        if nav_after is not None and floor_usd is not None:
            cushion_usd = nav_after - floor_usd
            r["cushion_to_floor_usd"] = round(cushion_usd, 0)
            r["cushion_to_floor_gbp"] = round(cushion_usd * float(gbp_per_usd), 0)
            r["breaches_floor"] = bool(cushion_usd < 0)
            r["thin_cushion"] = bool(0 <= cushion_usd * float(gbp_per_usd) < cushion_min)
            if r["breaches_floor"]:
                flags.append(f"{key}: net liquidation would fall BELOW the floor by £{-r['cushion_to_floor_gbp']:,.0f}")
            elif r["thin_cushion"]:
                flags.append(f"{key}: only £{r['cushion_to_floor_gbp']:,.0f} above the floor (minimum cushion £{cushion_min:,.0f})")
        else:
            r["cushion_to_floor_usd"] = None
            r["breaches_floor"] = None
            r["thin_cushion"] = None
        out_sc[key] = r
    gaps = []
    if nav_usd is None:
        gaps.append("net liquidation not supplied — scenarios show P&L only")
    if floor_usd is None:
        gaps.append("account floor or FX not available — the floor test cannot run")
    unk = sum(r["gamma_unknown_legs"] for r in out_sc.values()) // max(1, len(out_sc))
    if unk:
        gaps.append(f"{unk} option leg(s) without gamma — second-order term excluded for them")
    return {
        "version": "FOMC_STRESS_V1",
        "as_of": (today or date.today()).isoformat(),
        "nav_usd": nav_usd, "floor_gbp": floor_gbp, "floor_usd": round(floor_usd, 0) if floor_usd else None,
        "gbp_per_usd": gbp_per_usd, "cushion_min_gbp": cushion_min,
        "scenarios": out_sc, "flags": flags, "gaps": gaps,
        "events": events or [],
        "implied_moves": implied_moves or {},
        "hike_odds": hike_odds or {"available": False, "note": "no rate-odds feed; settings-kv macro.fomc_hike_odds_pct is the owner input"},
        "how": ("per segment: net delta-dollars × move + ½ × gamma-dollars-per-1% × move²; each segment moves by the "
                "scenario's move for its hedge ETF, else the default, else the index move × segment beta; "
                "hedges are inside each segment's net"),
        "placed_orders": 0,
    }


def implied_move_from_straddle(call_mid: float | None, put_mid: float | None, spot: float | None) -> float | None:
    """ATM straddle price as a % of spot ≈ the market's expected move to that expiry."""
    if not call_mid or not put_mid or not spot or spot <= 0:
        return None
    return round((call_mid + put_mid) / spot * 100.0, 2)
