"""Options Income Engine (INCOME_PUT_SPREADS_V1) — scan and monitor, no orders.

    tradepro-income-spreads scan                      # Stage A–F, print
    tradepro-income-spreads scan --push               # publish today-setups/income_put_spreads
    tradepro-income-spreads scan --symbols ABBV,JNJ   # on-demand, named underlyings
    tradepro-income-spreads monitor [--push]          # Stage H on the ledger's open spreads

Spec: strategies/INCOME_PUT_SPREADS_V1.md. Stage G (the IBKR order
instruction) is NOT here — TradePro never creates or submits a live order.
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

from .. import income_spreads as S


def _print_scan(art: dict) -> None:
    s, caps = art["settings"], art["caps"]
    print(f"INCOME ENGINE SCAN ({art['spec']}) — {art['as_of_utc']} · settings: {s['source']} · "
          f"fx {art['fx']['gbp_per_usd']:.4f} GBP/USD ({art['fx']['source']})")
    print(f"  caps: £{caps['open_total_gbp']:,.0f} open of £{caps['total_cap_gbp']:,} total; by sector "
          + ", ".join(f"{k} £{v:,.0f}" for k, v in caps["open_by_sector_gbp"].items()) + f" · held live: {len(art['held_live'])} names")
    if not art["candidates"]:
        print("  CANDIDATES: none passed every gate")
    for c in art["candidates"]:
        g = c["gates"]
        print(f"  CANDIDATE {c['id']}  score {c['score']:.2f} · {c['sector']}")
        print(f"    {c['expiry']} ({c['dte']} DTE) {c['short_strike']:g}/{c['long_strike']:g}P · credit mid {c['credit_mid']:.2f} "
              f"(natural {c['credit_natural']:.2f}, {c['credit_to_width']:.1%} of width) · ×{c['contracts']} → income {c['income_usd']:,.0f} $, "
              f"max loss {c['max_loss_usd']:,.0f} $ (£{c['max_loss_gbp']:,.0f}, {c['binding_cap']} cap)")
        hv_p = "—" if c.get("p_full_profit_hv") is None else f"{c['p_full_profit_hv']:.0%}"
        print(f"    P(full profit) IV {c['p_full_profit_iv']:.0%} / HV {hv_p} · "
              f"P(max loss) {c['p_max_loss_iv']:.0%} · IV {c['iv_short']:.1%} vs HV30 {c['hv_30d']:.1%} ({c['iv_hv_ratio']}) · "
              f"cushion {c['cushion_pct']:.1%} vs 1σ {c['one_sd_move_pct']:.1%} · BE {c['breakeven']}")
        print(f"    gates: 200d {g['sma200']} ✓ · cloud {g['cloud_top']} ✓ · swing low {g['swing_low_30d']} · RSI {g['rsi14']} · %B {g['bb_pct_b']} · "
              f"earnings {g['next_earnings']} ({g['earnings_basis']}) · bars {g['bars_as_of']} · quotes {c.get('quote_source')}")
        print(f"    exit: {c['exit_rule']} · take profit ≤ {c['take_profit_at']} · {c['instruction_hint']}")
        k = c.get("csp")
        if k:
            if k.get("eligible"):
                print(f"    CASH-SECURED alternative: {k['short_strike']:g}P ×{k['contracts']} credit {k['credit_mid']:.2f} → income {k['income_usd']:,.0f} $ "
                      f"on cash {k['cash_required_usd']:,.0f} $ (£{k['cash_required_gbp']:,.0f}) = {k['income_pct_of_cash']:.2f}% ({k['annualised_pct_of_cash']:.1f}%/yr) · "
                      f"P(full) {k['p_full_profit_iv']:.0%} · assignment risk to zero {k['max_loss_usd']:,.0f} $ · {k['instruction_hint']}")
            else:
                print(f"    CASH-SECURED alternative: not offered — {k.get('why')}")
    if art["rejected"]:
        print("  REJECTED:")
        for r in art["rejected"]:
            print(f"    {r['symbol']:5} {'; '.join(r['reasons'])}" + (f"  (warn: {'; '.join(r['warnings'])})" if r.get("warnings") else ""))
    print(f"  {art['note']}")


def _print_monitor(rows: list[dict]) -> None:
    print("INCOME ENGINE MONITOR — open spreads")
    if not rows:
        print("  no open trades in the ledger")
    for t in rows:
        print(f"  {t['symbol']:5} {t['role']:6} {t.get('expiry')} {t.get('short_strike')}/{t.get('long_strike')} ×{t.get('contracts')} "
              f"({t.get('dte')} DTE) close {t.get('close')} on {t.get('close_as_of')} · exit level {t.get('exit_level')} · "
              + (f"spread mid {t['spread_mid']} = {t['mark_pct_of_credit']:.0%} of credit" if t.get("spread_mid") is not None and t.get("mark_pct_of_credit") is not None
                 else (t.get("mark_note") or t.get("note") or "")))
        for a in t["alerts"]:
            print(f"      ** {a['kind']}: {a['detail']}")


def _publish(base: str, token: str, universe: str, art: dict, by: str) -> int:
    body = {"universe": universe, "label": "latest", "uploaded_by": by, "artifact": art}
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups", json=body,
                      headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=(5, 60))
    if r.status_code >= 300:
        print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    print(f"published {universe}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["scan", "monitor"])
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--symbols", help="scan only these underlyings (on-demand)")
    ap.add_argument("--json", action="store_true", help="dump the artifact")
    a = ap.parse_args(argv)
    from .push_to_api import load_credentials
    base, token = load_credentials()
    if a.mode == "scan":
        only = {x.strip().upper() for x in a.symbols.split(",") if x.strip()} if a.symbols else None
        art = S.scan(base, token, only=only)
        art["mode"] = "on_demand" if only else "daily"
        _print_scan(art)
        if a.json:
            print(json.dumps(art, indent=1, default=str))
        return _publish(base, token, S.ARTIFACT_UNIVERSE, art, "tradepro-income-spreads") if a.push else 0
    s = S.settings(base, token)
    rec = S.record(base, token)
    rows = S.monitor(rec, base, token, s)
    _print_monitor(rows)
    art = {"strategy": S.STRATEGY, "spec": S.SPEC, "open_spreads": rows,
           "alerts": [dict(a, symbol=t["symbol"]) for t in rows for a in t["alerts"]],
           "record": S.record_summary(rec, s), "placed_orders": 0}
    if a.json:
        print(json.dumps(art, indent=1, default=str))
    return _publish(base, token, S.ARTIFACT_UNIVERSE + "-monitor", art, "tradepro-income-spreads") if a.push else 0


if __name__ == "__main__":
    raise SystemExit(main())
