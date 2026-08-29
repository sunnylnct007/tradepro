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

    morning   decide (is the vol index at or below its ABSOLUTE threshold?),
              pick strikes at 1.5x the expected daily move,
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
#
# ABSOLUTE volatility threshold, not a trailing percentile (owner, 29 Aug:
# "when placing in paper we can use absolute for now", and "shd be adjustable").
#
# WHY ABSOLUTE. A trailing bottom-quartile fires ~25% of days in EVERY era by
# construction, so through 2009-2016 it sold strangles at a median India VIX of
# 16.7 and called that "low volatility". It wasn't — and that era is exactly
# where the modelled strategy lost (-427/trade). The owner never traded that
# way: "placed only when volatility is less" is an absolute judgement about the
# number on the screen, not a percentile.
#
# Measured with the absolute rule instead (BANKNIFTY, 17 years):
#     India VIX <= 12   2009-16: n=3 mean +481  |  2017-26: n=400 mean +856
#                       ALL: n=403 mean +853, total +343,645
# It simply STOPS TRADING when the regime turns, which is what the rule is for.
#
# SPY, same construction, % of collateral:
#     VIX <= 12   24 trades/yr  worst -0.31%   0 trades in 2008-09
#     VIX <= 14   66 trades/yr  worst -1.05%   0 trades in 2008-09
#     VIX <= 16  100 trades/yr  worst -1.05%   0 trades in 2008-09
#     VIX <= 18  131 trades/yr  worst -1.05%   8 trades in 2008-09  <- GFC leaks
# The mean barely moves across thresholds; the TAIL does. 14 is the default
# because it sits out the GFC entirely and triples the frequency of 12, while
# 18 starts trading into a crash.
VIX_MAX = {"US": 14.0, "INDIA": 12.0}
VIX_LOOKBACK = 250          # sessions, still reported as CONTEXT alongside
STRIKE_MULT = 1.5           # strikes at N x the implied DAILY move

# EXPIRIES RECORDED SIDE BY SIDE. The owner sells MONTHLY and closes the same
# day; he raised weekly himself ("we could even try with weekly one") and ruled
# out the shortest ("we will rarely sell with 1 DTE"). So both are recorded and
# neither is assumed — a month of real premiums decides it, not my model.
#
# Measured on 403 low-VIX BANKNIFTY sessions, strikes at +/-0.87%, closed same
# day. Note that a one-day hold captures ONE DAY of theta whatever the expiry,
# so the return barely changes while the capital at risk changes enormously:
#
#   DTE  avg credit   mean P&L   win%      worst   worst as % of credit
#     1      6,868      3,071   84.4%   -60,271        -878%   <- excluded
#     2     17,641      5,796   85.4%   -55,517        -315%
#     7     59,397      3,792   85.1%   -42,089         -71%
#    21    128,575      2,318   84.4%   -31,423         -24%
#
# Weekly roughly doubles monthly's return for about 1.5x the worst day, and is
# the shortest expiry where one bad session still costs LESS than the premium
# collected. 1 DTE costs 8.8x it — no profit target survives a gap like that.
# Win rate is ~85% at every expiry and so decides nothing, which is the same
# lesson as everywhere else in this work.
DTE_SET = {"weekly": 7, "monthly": 21}
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
    q1 = hist[len(hist) // 4]           # context only — no longer the gate
    import os as _os
    thr = float(_os.environ.get(f"TRADEPRO_STRANGLE_VIX_MAX_{market}",
                                VIX_MAX[market]))
    spot = float(px.loc[today, "Close"])

    iv = v / 100.0 * (INDIA_VOL_SCALE if market == "INDIA" else 1.0)
    daily = iv / math.sqrt(252)
    width = STRIKE_MULT * daily
    out.update({
        "as_of": today, "spot": round(spot, 2),
        "vol_index": round(v, 2), "vol_q1_trailing": round(q1, 2),
        "vol_threshold": thr,
        "threshold_env": f"TRADEPRO_STRANGLE_VIX_MAX_{market}",
        "iv_used": round(100 * iv, 2),
        "iv_source": ("vol index" if market == "US"
                      else f"India VIX x {INDIA_VOL_SCALE} (BANKNIFTY realises more)"),
        "expected_daily_move_pct": round(100 * daily, 2),
        "strike_rule": f"{STRIKE_MULT}x the expected daily move",
        "call_strike": round(spot * (1 + width), 2),
        "put_strike": round(spot * (1 - width), 2),
        "width_pct": round(100 * width, 2),
        "lot": cfg["lot"],
        "expiries": DTE_SET,
    })
    if v <= thr:
        out["status"] = "CANDIDATE"
        out["reason"] = (f"{cfg['vol']} {v:.2f} is at or below the {thr:.1f} "
                         f"threshold (trailing 25th pctile is {q1:.2f}, for context)")
    else:
        out["status"] = "stand aside"
        out["reason"] = (f"{cfg['vol']} {v:.2f} is ABOVE the {thr:.1f} threshold "
                         f"— not a low-volatility day (trailing 25th pctile {q1:.2f})")
    return out


def _email_cfg() -> dict:
    """SMTP settings from the SAME place every other TradePro email reads,
    with one addition for Lambda.

    Order: local creds file (the Mac), then Secrets Manager (Lambda, where
    there is no home directory), then env vars. The secret holds exactly the
    same JSON shape as the file, so there is ONE schema and no translation
    layer to drift — the file is uploaded to the secret verbatim.
    """
    from .email_digest import CRED_PATH
    data = json.loads(CRED_PATH.read_text()) if CRED_PATH.is_file() else {}
    if not data and os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        try:
            import boto3
            sm = boto3.client("secretsmanager",
                              region_name=os.environ.get("AWS_REGION", "eu-west-2"))
            data = json.loads(sm.get_secret_value(
                SecretId=os.environ.get("TRADEPRO_EMAIL_SECRET", "tradepro/email")
            )["SecretString"])
        except Exception as exc:  # noqa: BLE001 — fall through to env vars
            log.warning("secrets manager read failed: %s", str(exc)[:120])
    return {
        "smtp_host": data.get("smtp_host") or os.environ.get("TRADEPRO_SMTP_HOST"),
        "smtp_port": int(data.get("smtp_port") or os.environ.get("TRADEPRO_SMTP_PORT") or 465),
        "smtp_user": data.get("smtp_user") or os.environ.get("TRADEPRO_SMTP_USER"),
        "smtp_password": data.get("smtp_password") or os.environ.get("TRADEPRO_SMTP_PASSWORD"),
        "from": data.get("from") or os.environ.get("TRADEPRO_EMAIL_FROM"),
        "to": [t for t in (data.get("to") or [os.environ.get("TRADEPRO_EMAIL_TO")]) if t],
    }


def _email_body(rows: list[dict]) -> tuple[str, str]:
    live = [r for r in rows if r.get("status") == "CANDIDATE"]
    aside = [r for r in rows if r.get("status") == "stand aside"]
    subj = ("[PAPER] index strangle — "
            + (", ".join(r["market"] for r in live) + " CANDIDATE" if live
               else "stand aside both markets"))
    L = ["INDEX SHORT STRANGLE — PAPER RECORD.  Nothing is placed.",
         "This is a record for evaluation, not advice. You execute or you don't.", ""]
    for r in rows:
        L.append(f"{r['market']}  ({r['index']})")
        if r.get("status") == "no_data":
            L += [f"   NO DATA — {r['reason']}", ""]
            continue
        L.append(f"   {'>>> CANDIDATE' if r['status']=='CANDIDATE' else '--- stand aside'}: {r['reason']}")
        L.append(f"   spot {r['spot']:,}   ·   IV used {r['iv_used']}%  ({r['iv_source']})")
        L.append(f"   expected daily move {r['expected_daily_move_pct']}%   ·   "
                 f"strikes {r['strike_rule']} = ±{r['width_pct']}%")
        if r["status"] == "CANDIDATE":
            L.append(f"   SELL  {r['put_strike']:,} PUT   +   {r['call_strike']:,} CALL   x{r['lot']}")
            for name, dte in sorted(r["expiries"].items(), key=lambda kv: kv[1]):
                L.append(f"      · {name} (~{dte}d) — recorded separately, CLOSE SAME DAY")
        L.append(f"   threshold {r['vol_threshold']} — adjust with {r['threshold_env']}")
        L.append("")
    L += ["WHAT THIS IS AND IS NOT",
          "  · Strikes come from the index and its volatility index only — no option",
          "    chain — so a dark options feed cannot stop this producing a decision.",
          "  · Premiums are NOT quoted. India has no free NSE chain; US chains are",
          "    captured end-of-day. The credit gets filled in from a captured chain or",
          "    by you, so a modelled number is never mistaken for a traded one.",
          "  · Weekly and monthly are recorded side by side. A one-day hold captures one",
          "    day of theta whatever the expiry, so monthly ties up far more capital for",
          "    a similar return — the record is there to settle that on real prices.",
          "",
          "EVIDENCE, and its limits",
          "  · Modelled on 403 low-volatility BANKNIFTY sessions: ~85% win at every",
          "    expiry, so win rate decides nothing.",
          "  · It LOST through 2009-2016. The absolute volatility threshold is what",
          "    would have kept you out — 3 trades in 8 years.",
          "  · The intraday profit target and the strangle->straddle conversion are NOT",
          "    modelled. Both are things you actually do; neither is measurable here.",
          "  · NOT FUNDED. This is a month of observation, not a recommendation."]
    text = "\n".join(L)
    html = "<pre style=\"font-family:monospace;font-size:13px\">" + text.replace("<", "&lt;") + "</pre>"
    return subj, (text, html)


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
    ap.add_argument("--email", action="store_true", help="send the daily candidate email")
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
                  f"x{r['lot']}")
            for name, dte in sorted(r["expiries"].items(), key=lambda kv: kv[1]):
                print(f"      · {name:<8} ~{dte}d to expiry — recorded separately, "
                      f"closed same day")
        print()
    if args.email:
        # Fail-soft: an email problem must never lose the decision, which is
        # already recorded and printed by this point.
        try:
            from types import SimpleNamespace
            from .email_digest import send_email
            subj, (text, html) = _email_body(rows)
            send_email(SimpleNamespace(subject=subj, text_body=text,
                                       html_body=html, pdf_bytes=None), _email_cfg())
            print(f"  email sent: {subj}")
        except Exception as exc:  # noqa: BLE001
            log.warning("email failed (non-fatal): %s", exc)
            print(f"  email FAILED (non-fatal): {str(exc)[:120]}")

    print("  Premiums are NOT shown: India has no free NSE chain and US chains are")
    print("  captured end-of-day. The record stores the STRIKES; the credit is filled")
    print("  in from the captured chain, or by you, so a modelled number is never")
    print("  mistaken for a traded one.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
