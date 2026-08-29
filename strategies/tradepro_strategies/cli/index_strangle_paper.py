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

# Strike GRID and risk-free rate per market. Two bugs this fixes, both real:
#
# 1. STRIKES WERE CENTRED ON SPOT, NOT THE FORWARD. Index options price off the
#    forward, and with Indian rates ~6.5% the 21-day BANKNIFTY forward sits
#    ~215 points above spot. Centring on spot therefore pushed the put further
#    out than the call — measured on the 29 Aug record: 1,025 points to the put
#    against 575 to the call, nearly 2:1. That is unintentional upside risk and
#    it is free to remove.
# 2. THE STRIKES WERE NOT TRADEABLE. "56,697.84 PUT" does not exist; BANKNIFTY
#    lists on a 100-point grid. Printing an unlistable strike in an email that
#    asks someone to place a trade is the kind of detail that destroys trust in
#    everything around it.
RATE = {"US": 0.045, "INDIA": 0.065}
STRIKE_GRID = {"US": 1.0, "INDIA": 100.0}

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
    rate = RATE[market]
    grid = STRIKE_GRID[market]

    def _strikes(dte: int) -> tuple[float, float]:
        """Put and call, centred on the FORWARD and snapped to the listed grid."""
        fwd = spot * math.exp(rate * dte / 365.0)
        return (round(fwd * (1 - width) / grid) * grid,
                round(fwd * (1 + width) / grid) * grid)
    out.update({
        "as_of": today, "spot": round(spot, 2),
        "vol_index": round(v, 2), "vol_q1_trailing": round(q1, 2),
        "vol_threshold": thr,
        "threshold_env": f"TRADEPRO_STRANGLE_VIX_MAX_{market}",
        "iv_used": round(100 * iv, 2),
        "iv_source": ("vol index" if market == "US"
                      else f"India VIX x {INDIA_VOL_SCALE} (BANKNIFTY realises more)"),
        "expected_daily_move_pct": round(100 * daily, 2),
        "strike_rule": f"{STRIKE_MULT}x the expected daily move, centred on the forward",
        "width_pct": round(100 * width, 2),
        "lot": cfg["lot"],
        "expiries": DTE_SET,
        # One pair of strikes PER EXPIRY — the forward differs, so 7d and 21d
        # do not share strikes. Treating them as one pair is what produced the
        # lopsided monthly.
        "legs": {name: {"dte": dte,
                        "put_strike": _strikes(dte)[0],
                        "call_strike": _strikes(dte)[1],
                        "forward": round(spot * math.exp(rate * dte / 365.0), 2)}
                 for name, dte in DTE_SET.items()},
    })
    _wk = out["legs"]["weekly"]
    out["put_strike"], out["call_strike"] = _wk["put_strike"], _wk["call_strike"]
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


def _email_body(rows: list[dict]) -> tuple[str, tuple[str, str]]:
    """Subject, and (plain-text, HTML) bodies.

    The first version was one <pre> block — everything in the same weight, the
    actual trade buried among caveats. Owner: "can we do proper formatting".
    So the HTML is built to be SCANNED on a phone: the trade first at size,
    the reasoning under it, the limits last and quiet. The plain-text part is
    kept as a real fallback, not an afterthought, because some clients will
    only ever render that.
    """
    live = [r for r in rows if r.get("status") == "CANDIDATE"]
    subj = ("[PAPER] index strangle — "
            + (", ".join(r["market"] for r in live) + " CANDIDATE" if live
               else "stand aside, both markets"))

    # ---- plain text (fallback) ----
    T = ["INDEX SHORT STRANGLE - PAPER RECORD (nothing is placed)", ""]
    for r in rows:
        if r.get("status") == "no_data":
            T += [f"{r['market']}: NO DATA - {r['reason']}", ""]
            continue
        if r["status"] == "CANDIDATE":
            T += [f"{r['market']}: TRADE   (spot {r['spot']:,} · {r['index']})"]
            for n, l in sorted(r["legs"].items(), key=lambda kv: kv[1]["dte"]):
                T.append(f"  {n:<8} ~{l['dte']:>2}d   SELL {l['put_strike']:,.0f} PUT"
                         f" + {l['call_strike']:,.0f} CALL   x{r['lot']}")
            T += ["  both CLOSED SAME DAY · strikes centred on the FORWARD, on the listed grid",
                  f"  {r['reason']}", ""]
        else:
            T += [f"{r['market']}: STAND ASIDE", f"  {r['reason']}", ""]
    T += ["Premiums are not quoted - the credit is filled in from a captured",
          "chain or by you, so a modelled number is never mistaken for a traded one.",
          "NOT FUNDED. A month of observation, not a recommendation."]
    text = "\n".join(T)

    # ---- html ----
    D = "#0f1729"; MUT = "#5b6779"; LINE = "#e3e8ef"
    OK = "#0f8a5f"; OFF = "#8b95a5"; WARN = "#b26a00"
    H = [f'<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;'
         f'max-width:600px;margin:0 auto;color:{D};font-size:15px;line-height:1.5">']
    H.append(f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;'
             f'color:{MUT};padding-bottom:4px">Index short strangle · paper record</div>')

    for r in rows:
        if r.get("status") == "no_data":
            H.append(f'<div style="border:1px solid {LINE};border-radius:10px;padding:14px;'
                     f'margin:12px 0"><b>{r["market"]}</b><br>'
                     f'<span style="color:{WARN}">No data — {r["reason"]}</span></div>')
            continue
        is_c = r["status"] == "CANDIDATE"
        accent = OK if is_c else OFF
        H.append(f'<div style="border:1px solid {LINE};border-left:4px solid {accent};'
                 f'border-radius:10px;padding:16px;margin:14px 0">')
        H.append(f'<div style="display:block"><span style="font-size:17px;font-weight:700">'
                 f'{r["market"]}</span> <span style="color:{MUT};font-size:13px">'
                 f'{r["index"]} · {r["spot"]:,}</span></div>')
        if is_c:
            H.append(f'<div style="background:#f2faf6;border-radius:8px;padding:14px;margin:12px 0">'
                     f'<div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;'
                     f'color:{OK};font-weight:700;padding-bottom:8px">Sell · x{r["lot"]} · '
                     f'close same day</div>')
            for n, l in sorted(r["legs"].items(), key=lambda kv: kv[1]["dte"]):
                H.append(f'<div style="padding:6px 0">'
                         f'<span style="font-size:12px;color:{MUT};text-transform:uppercase;'
                         f'letter-spacing:.06em">{n} · ~{l["dte"]}d</span><br>'
                         f'<span style="font-size:19px;font-weight:700;'
                         f'font-variant-numeric:tabular-nums">'
                         f'{l["put_strike"]:,.0f} PUT &nbsp;+&nbsp; {l["call_strike"]:,.0f} CALL'
                         f'</span></div>')
            H.append(f'<div style="color:{MUT};font-size:12px;padding-top:6px">'
                     f'centred on the forward ({r["legs"]["weekly"]["forward"]:,.0f} / '
                     f'{r["legs"]["monthly"]["forward"]:,.0f}), snapped to the listed grid'
                     f'</div></div>')
            H.append('<table style="width:100%;border-collapse:collapse;font-size:13px">'
                     + "".join(
                         f'<tr><td style="padding:3px 0;color:{MUT}">{k}</td>'
                         f'<td style="padding:3px 0;text-align:right;font-variant-numeric:tabular-nums">'
                         f'{v}</td></tr>'
                         for k, v in (("expected daily move", f"{r['expected_daily_move_pct']}%"),
                                      ("strike width", f"±{r['width_pct']}%"),
                                      ("IV used", f"{r['iv_used']}%")))
                     + "</table>")
        else:
            H.append(f'<div style="color:{OFF};font-weight:600;padding:8px 0">Stand aside</div>')
        H.append(f'<div style="color:{MUT};font-size:12.5px;padding-top:8px;'
                 f'border-top:1px solid {LINE};margin-top:10px">{r["reason"]}</div>')
        H.append("</div>")

    H.append(f'<div style="color:{MUT};font-size:12.5px;line-height:1.6;padding-top:8px">'
             f'<b style="color:{D}">Premiums are not quoted.</b> The credit is filled in from a '
             f'captured chain or by you, so a modelled number is never mistaken for a traded one.'
             f'<br><br>'
             f'<b style="color:{D}">Limits.</b> ~85% win at every expiry, so win rate decides '
             f'nothing. It LOST through 2009–2016; the volatility threshold is what would have '
             f'kept you out — 3 trades in 8 years. The intraday profit target and the '
             f'strangle→straddle conversion are not modelled, and both are things you actually do.'
             f'</div>')
    H.append(f'<div style="margin-top:14px;padding:10px 12px;background:#fff7ed;'
             f'border-radius:8px;color:{WARN};font-size:13px;font-weight:600">'
             f'NOT FUNDED — a month of observation, not a recommendation. '
             f'Nothing is placed by TradePro.</div></div>')
    return subj, (text, "".join(H))


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
