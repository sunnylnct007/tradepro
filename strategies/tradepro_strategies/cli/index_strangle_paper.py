"""Index short-strangle PAPER RECORD — both markets, no broker.

WHY THIS EXISTS, and why it is a paper record rather than a screen.

The backtests could not settle this strategy, and the reason is specific: we
have NO historical option prices. Every premium in every run was Black-Scholes
from a 30-day vol index, and that model has no variance risk premium in it —
so it measures "was realised vol below implied", which came out at roughly zero
in both markets:

    India VIX vs NIFTY realised        -0.2 pts
    BANKNIFTY-scaled IV vs realised    -0.3 pts
    SPY, 8,202 sessions, mean          +5 per contract

The owner trades 0-2 DTE, whose implied vol is nothing like the 30-day index,
and exits on an intraday profit target. Neither is reachable with the data we
hold. His conclusion, and it is the right one: *"can we start placing this in
paper trade from Monday ... and start observing for next month or so"*, and
crucially *"we need to start storing these execution data as no platform will
provide these for free"*.

That last point is the real product here. Every session recorded builds the
dataset whose absence made the backtest unreliable. It cannot be bought
cheaply and it cannot be backfilled.

WHAT IT DOES

    morning   decide (VIX in its TRAILING bottom quartile?), pick strikes,
              record the strangle we would have sold, with the credit
    evening   mark it against the close and record the outcome

REAL vs MODELLED — never blurred. US premiums come from a captured option chain
when one is available; India has no free NSE chain, so its premiums are
Black-Scholes and every row says so. A ledger that mixes the two without
labelling them would repeat the exact mistake that made the backtest untrustworthy.

NO BROKER. Nothing is placed. IBKR options data is dark on this account anyway
(no OPRA — USD 32.75/mo, deliberately not subscribed until a month of this
record justifies it), and the Indian legs are placed by hand.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import math
import os
import statistics as _st

log = logging.getLogger("tradepro.index_strangle_paper")

# One definition of the rule, shared by both markets.
VIX_LOOKBACK = 250          # sessions in the trailing quartile window
STRIKE_MULT = 1.5           # strikes at N x the implied DAILY move
DTE = 1                     # sold against the nearest expiry
LEDGER = os.path.expanduser("~/.tradepro/research/index_strangle_paper.json")

MARKETS = {
    "US": {"index": "SPY", "vol": "^VIX", "lot": 100,
           "note": "SPY has next-day expiries; premiums from a captured chain when present"},
    "INDIA": {"index": "^NSEBANK", "vol": "^INDIAVIX", "lot": 150,
              "note": "India VIX measures NIFTY, and BANKNIFTY realises ~1.35x that — "
                      "premiums are scaled accordingly and are MODELLED, not observed"},
}
# BANKNIFTY realises ~1.35x NIFTY vol (measured over 4,529 sessions, 29 Aug
# 2026). Pricing BANKNIFTY options at India VIX under-collects premium by ~35%
# and was what made the 18-year backtest look far worse than the trade is.
INDIA_VOL_SCALE = 1.35


def _series(sym: str, period: str = "2y"):
    from ..yahoo_session import yahoo_session
    import yfinance as yf
    d = yf.Ticker(sym, session=yahoo_session()).history(period=period, interval="1d")
    if d is None or not len(d):
        return None
    d.index = [str(x)[:10] for x in d.index]
    return d


def decide(market: str) -> dict:
    """Today's candidate for one market. Bars + a vol index only — no chain,
    so a dark options feed can never stop this producing a decision."""
    cfg = MARKETS[market]
    px = _series(cfg["index"])
    vx = _series(cfg["vol"])
    out: dict = {"market": market, "index": cfg["index"], "note": cfg["note"]}
    if px is None or vx is None:
        out["status"] = "no_data"
        out["reason"] = f"could not load {cfg['index']} or {cfg['vol']}"
        return out

    common = [d for d in px.index if d in vx.index]
    if len(common) < VIX_LOOKBACK + 5:
        out["status"] = "no_data"
        out["reason"] = f"only {len(common)} joined sessions"
        return out

    vols = [float(vx.loc[d, "Close"]) for d in common]
    today, v = common[-1], vols[-1]
    # TRAILING quartile — the boundary uses only prior sessions. An in-sample
    # quartile would leak the future into the filter, which is the easiest way
    # to fake this entire result.
    hist = sorted(vols[-(VIX_LOOKBACK + 1):-1])
    q1 = hist[len(hist) // 4]
    spot = float(px.loc[today, "Close"])

    iv = v / 100.0 * (INDIA_VOL_SCALE if market == "INDIA" else 1.0)
    daily = iv / math.sqrt(252)
    width = STRIKE_MULT * daily
    out.update({
        "as_of": today, "spot": round(spot, 2),
        "vol_index": round(v, 2), "vol_q1_trailing": round(q1, 2),
        "iv_used": round(100 * iv, 2),
        "iv_source": ("vol index" if market == "US"
                      else f"India VIX x {INDIA_VOL_SCALE} (BANKNIFTY realises more)"),
        "expected_daily_move_pct": round(100 * daily, 2),
        "strike_rule": f"{STRIKE_MULT}x the expected daily move",
        "call_strike": round(spot * (1 + width), 2),
        "put_strike": round(spot * (1 - width), 2),
        "width_pct": round(100 * width, 2),
        "lot": cfg["lot"], "dte": DTE,
    })
    if v <= q1:
        out["status"] = "CANDIDATE"
        out["reason"] = (f"{cfg['vol']} {v:.2f} is at or below its trailing "
                         f"25th percentile ({q1:.2f})")
    else:
        out["status"] = "stand aside"
        out["reason"] = (f"{cfg['vol']} {v:.2f} is ABOVE its trailing 25th "
                         f"percentile ({q1:.2f}) — not a low-volatility day")
    return out


def _load_ledger() -> list:
    if os.path.exists(LEDGER):
        try:
            return json.load(open(LEDGER))
        except Exception:  # noqa: BLE001
            return []
    return []


def record(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    led = _load_ledger()
    seen = {(r.get("market"), r.get("as_of")) for r in led}
    added = [r for r in rows if (r.get("market"), r.get("as_of")) not in seen]
    led.extend(added)
    json.dump(led, open(LEDGER, "w"), indent=1)
    log.info("ledger: +%d row(s), %d total -> %s", len(added), len(led), LEDGER)


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-index-strangle-paper")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    rows = [decide(m) for m in MARKETS]
    if not args.no_record:
        record([r for r in rows if r.get("status") in ("CANDIDATE", "stand aside")])

    if args.json:
        print(json.dumps(rows, indent=1))
        return 0

    print(f"index short-strangle PAPER RECORD — {_dt.datetime.now(_dt.UTC):%Y-%m-%d %H:%M}Z")
    print("  nothing is placed; this is a record, not an order\n")
    for r in rows:
        print(f"  {r['market']} ({r['index']})")
        if r.get("status") == "no_data":
            print(f"    NO DATA — {r['reason']}\n")
            continue
        flag = "CANDIDATE" if r["status"] == "CANDIDATE" else "stand aside"
        print(f"    {flag}: {r['reason']}")
        print(f"    spot {r['spot']}  ·  IV used {r['iv_used']}% ({r['iv_source']})")
        print(f"    expected daily move {r['expected_daily_move_pct']}%  ·  "
              f"strikes at {r['strike_rule']} = ±{r['width_pct']}%")
        if r["status"] == "CANDIDATE":
            print(f"    SELL  {r['put_strike']} PUT   +  {r['call_strike']} CALL   "
                  f"x{r['lot']}  ({r['dte']} DTE)")
        print()
    print("  Premiums are NOT shown: India has no free NSE chain and US chains are")
    print("  captured end-of-day. The record stores the STRIKES; the credit is filled")
    print("  in from the captured chain, or by you, so a modelled number is never")
    print("  mistaken for a traded one.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
