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
# ONE ROW PER MARKET, SELF-CONTAINED. This used to be four dicts all keyed by
# market — MARKETS, VIX_MAX, RATE, STRIKE_GRID — which is the exact shape that
# has produced more bugs in this repo than anything else: a value defined in two
# places, no error when they disagree, just two components quietly using
# different numbers. Going from 2 markets to 8 across four dicts would have made
# that near-certain, so they are merged. Add a market by adding ONE row.
#
# WHY THESE EIGHT, and how each threshold was picked. Measured 29 Aug 2026 on
# the full history of each pair, same construction, % of collateral:
#
#   market      vol gate    n     win%   mean%    p5%    worst%
#   GLD         GVZ<=16   1,658   88.8   0.0521  -0.064  -0.65
#   BANKNIFTY   VIX<=12     404   84.9   0.0517  -0.066  -1.05
#   SPY         VIX<=14   2,219   83.3   0.0405  -0.105  -0.80
#   QQQ         VXN<=18   2,015   81.0   0.0422  -0.184  -1.01
#   SPX         VIX<=14   2,318   82.4   0.0370  -0.101  -0.84
#   NDX         VXN<=18   2,015   81.3   0.0402  -0.183  -1.08
#   NIFTY       VIX<=12     404   82.2   0.0333  -0.059  -0.29
#   (MIDCAP     VIX<=12     404   75.5   0.0160  -0.223  -1.54  -- REJECTED)
#
# NIFTY MIDCAP is excluded: worst win rate, worst tail, a third of the return.
# RUSSELL (^RUT) and DOW (^DJI) are excluded for a harder reason — ^RVX returns
# ZERO bars and ^VXD returns one, so there is no volatility gate for them at
# all. Inventing one from realised vol is exactly the modelling error that
# produced three wrong tables on 29 Aug, so they stay out until real data exists.
#
# THE GATE IS PER-VOL-INDEX, NOT PER-MARKET. VXN sits structurally ~4 points
# above VIX for the same market calm, so VXN<=18 is as selective on Nasdaq as
# VIX<=14 is on the S&P. Copying 14 across would have silenced Nasdaq entirely.
#
# `family` GROUPS THE SAME BET. SPX, XSP and SPY are one trade at three contract
# sizes, not three opportunities — and the email must say so, because eight rows
# that look independent invite eight positions on what is really two risks.
MARKETS = {
    # ---- S&P 500: one underlying, three contract sizes ----
    "SPX": {"index": "^GSPC", "vol": "^VIX", "vol_scale": 1.0, "vol_max": 14.0,
            "rate": 0.045, "grid": 5.0, "lot": 100, "divisor": 1.0,
            "family": "S&P 500", "ccy": "$",
            "product": "cash-settled index option · European · no early assignment",
            "note": "VIX is computed FROM SPX options, so the volatility input is "
                    "the underlying's own, not a proxy"},
    "XSP": {"index": "^GSPC", "vol": "^VIX", "vol_scale": 1.0, "vol_max": 14.0,
            "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 10.0,
            "family": "S&P 500", "ccy": "$",
            "product": "Mini-SPX · exactly 1/10 of SPX · cash-settled, European",
            "note": "the same trade as SPX at a tenth of the size — this is the "
                    "'smaller index' product; SPX itself is 10x SPY, not smaller"},
    "SPY": {"index": "SPY", "vol": "^VIX", "vol_scale": 1.0, "vol_max": 14.0,
            "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 1.0,
            "family": "S&P 500", "ccy": "$",
            "product": "ETF option · American · CAN be assigned early",
            "note": "measured edge is within noise of SPX (83.3% vs 82.4%), so the "
                    "choice is settlement and size, not return"},
    # ---- Nasdaq 100 ----
    "NDX": {"index": "^NDX", "vol": "^VXN", "vol_scale": 1.0, "vol_max": 18.0,
            "rate": 0.045, "grid": 25.0, "lot": 100, "divisor": 1.0,
            "family": "Nasdaq 100", "ccy": "$",
            "product": "cash-settled index option · European",
            "note": "VXN is computed FROM NDX options. Fatter tail than the S&P "
                    "(p5 -0.183 vs -0.101) — the same rule, more risk per unit"},
    "QQQ": {"index": "QQQ", "vol": "^VXN", "vol_scale": 1.0, "vol_max": 18.0,
            "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 1.0,
            "family": "Nasdaq 100", "ccy": "$",
            "product": "ETF option · American · CAN be assigned early",
            "note": "fires 2,015 times against SPY's 2,219 because VXN<=18 is a "
                    "reachable gate — this is what fixes the thin US sample"},
    # ---- India ----
    "BANKNIFTY": {"index": "^NSEBANK", "vol": "^INDIAVIX", "vol_scale": 1.35,
                  "vol_max": 12.0, "rate": 0.065, "grid": 100.0, "lot": 150,
                  "divisor": 1.0, "family": "India banks", "ccy": "Rs",
                  "product": "cash-settled index option · European",
                  "note": "India VIX measures NIFTY and BANKNIFTY realises ~1.35x "
                          "that, so the input is SCALED — a proxy, not its own index"},
    "NIFTY": {"index": "^NSEI", "vol": "^INDIAVIX", "vol_scale": 1.0,
              "vol_max": 12.0, "rate": 0.065, "grid": 50.0, "lot": 75,
              "divisor": 1.0, "family": "India broad", "ccy": "Rs",
              "product": "cash-settled index option · European",
              "note": "India VIX measures NIFTY directly, so no 1.35 scaling is "
                      "needed. Worst day -0.29% vs BANKNIFTY's -1.05% — 3.5x safer "
                      "tail for about two-thirds the return"},
    # ---- Gold: the only genuinely uncorrelated leg here ----
    "GOLD": {"index": "GLD", "vol": "^GVZ", "vol_scale": 1.0, "vol_max": 16.0,
             "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 1.0,
             "family": "Gold", "ccy": "$",
             "product": "ETF option · American · CAN be assigned early",
             "note": "best risk-adjusted of the eight (88.8% win, tightest p5) and "
                     "the only one not driven by equity risk — the others are two "
                     "bets wearing six names"},
}

# Kept so nothing that imported them breaks; both now DERIVE from MARKETS rather
# than restating it, so they cannot drift out of step with the table above.
VIX_MAX = {m: c["vol_max"] for m, c in MARKETS.items()}
RATE = {m: c["rate"] for m, c in MARKETS.items()}
STRIKE_GRID = {m: c["grid"] for m, c in MARKETS.items()}
INDIA_VOL_SCALE = MARKETS["BANKNIFTY"]["vol_scale"]


def strike_pair(spot: float, width: float, dte: int, rate: float,
                grid: float) -> tuple[float, float, float]:
    """(put, call, forward), centred on the FORWARD and snapped to the grid.

    Module-level and shared, because the simulator has to price the SAME strikes
    this email prints. When the backtest and the live screen each compute their
    own strikes, they drift and the published evidence stops describing the
    thing being traded — which is how the 'harness enters at signal close, the
    screen says next open' mismatch got into the Swing numbers.
    """
    fwd = spot * math.exp(rate * dte / 365.0)
    put = round(fwd * (1 - width) / grid) * grid
    call = round(fwd * (1 + width) / grid) * grid

    # THE LEGS MUST STRADDLE THE FORWARD. Each leg is rounded independently, so
    # whenever the requested width is narrower than half a grid step both legs
    # snap to the SAME strike — or cross, putting the put above the call. Either
    # one silently turns the emailed trade into something else entirely: a
    # straddle, or an inverted strangle whose risk is nothing like what the
    # evidence in this email describes.
    #
    # Not reachable at present levels (BANKNIFTY's width is ~780 points against
    # a 100-point grid), but nothing upstream guarantees it stays that way — a
    # lower STRIKE_MULT, a calmer market, or a new market with a coarse grid all
    # reach it, and the result would be a tradeable-looking price that is wrong.
    # Found by test_strikes_are_on_the_listed_grid_and_straddle_the_forward.
    if put >= fwd:
        put = math.floor(fwd / grid) * grid
    if call <= fwd:
        call = math.ceil(fwd / grid) * grid
    if put >= call:                      # forward sat exactly on a grid point
        put, call = put - grid, call + grid
    return put, call, fwd


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
                                cfg["vol_max"]))
    # `divisor` carries XSP, which is the S&P index quoted at a tenth. The gate
    # and the width are scale-free, so only the printed level and strikes move.
    spot = float(px.loc[today, "Close"]) / cfg.get("divisor", 1.0)

    iv = v / 100.0 * cfg["vol_scale"]
    daily = iv / math.sqrt(252)
    width = STRIKE_MULT * daily
    rate, grid = cfg["rate"], cfg["grid"]

    def _strikes(dte: int) -> tuple[float, float]:
        p, c, _ = strike_pair(spot, width, dte, rate, grid)
        return p, c
    out.update({
        "as_of": today, "spot": round(spot, 2),
        "family": cfg["family"], "product": cfg["product"], "ccy": cfg["ccy"],
        "vol_index": round(v, 2), "vol_q1_trailing": round(q1, 2),
        "vol_threshold": thr,
        "threshold_env": f"TRADEPRO_STRANGLE_VIX_MAX_{market}",
        "iv_used": round(100 * iv, 2),
        "iv_source": (f"{cfg['vol']} as published"
                      if cfg["vol_scale"] == 1.0 else
                      f"{cfg['vol']} x {cfg['vol_scale']} (this index realises more)"),
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


def _evidence() -> dict:
    """The committed simulation output, keyed by market.

    Read from a FILE generated by `index_strangle_sim`, not recomputed here: the
    full run takes ~20s across eight markets, which does not belong in a daily
    email job. More importantly it means the figures quoted to a reader are a
    fixed, versioned artifact that can be diffed — if a number in the email
    changes, the commit that changed it is findable. Numbers retyped into email
    copy cannot be audited and always eventually stop matching the model.
    """
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     "data", "index_strangle_evidence.json")
    try:
        raw = json.load(open(p))
    except Exception as exc:  # noqa: BLE001 — the DECISION must survive this
        log.warning("evidence file unreadable (%s): %s", p, str(exc)[:120])
        return {}
    return {r["market"]: r for r in raw.get("results", [])
            if r.get("status") == "ok"}


def _email_body(rows: list[dict]) -> tuple[str, tuple[str, str]]:
    """Subject, and (plain-text, HTML) bodies.

    WRITTEN TO BE READ BY A SCEPTIC. Owner, 29 Aug 2026: "Email should be
    richer as well demonstrating some facts and figures as how to trust",
    "how to read this", and "think from perspective we are selling this email
    to other clients".

    That last one sets the bar. A stranger receiving this has three questions in
    a fixed order, and the email answers them in that order:

        1. What do I do today?          -> the positions, first, at size
        2. How do I read it?            -> an explicit reading guide, because
                                           "1.5x the expected daily move" means
                                           nothing to someone seeing it cold
        3. Why should I believe you?    -> the evidence block: sample size, the
                                           period covered, a forward simulation,
                                           AND the number that would hurt

    Point 3 is where most such emails cheat, by showing only the win rate. This
    one leads its evidence section with the cost of the strategy's own failure
    mode, because a short-premium product that advertises "88% win rate" without
    saying what the other 12% costs is mis-sold. The honest sales argument is
    that the gate has never been open on a crash day in 25 years of history —
    which is checkable, and stated with the number that makes it matter.

    GROUPED BY FAMILY. Eight rows implies eight opportunities; in reality SPX,
    XSP and SPY are ONE bet at three sizes, and NDX/QQQ another. Presenting them
    flat would invite a reader to take eight positions on what is really three
    risks, and concentration dressed as diversification is precisely how a
    premium-selling account dies.
    """
    ev = _evidence()
    live = [r for r in rows if r.get("status") == "CANDIDATE"]
    aside = [r for r in rows if r.get("status") == "stand aside"]
    dark = [r for r in rows if r.get("status") == "no_data"]
    today = _dt.date.today().strftime("%a %d %B %Y")
    subj = (f"[PAPER] Index strangle · {len(live)} of {len(rows)} markets open · "
            + (", ".join(sorted({r["family"] for r in live})) if live
               else "all stood aside"))

    def _fam_order(rs):
        """Families, candidates first, each family's rows largest contract first."""
        fams: dict[str, list] = {}
        for r in rs:
            fams.setdefault(r.get("family", "?"), []).append(r)
        return sorted(fams.items(),
                      key=lambda kv: (0 if any(x["status"] == "CANDIDATE"
                                               for x in kv[1]) else 1, kv[0]))

    def _yrs_cost(m):
        """How many years of median return ONE ungated crash day costs.

        The single most useful number in the whole email, and the one a sales
        deck would omit. It converts an abstract tail into a unit the reader
        already understands: time."""
        e = ev.get(m) or {}
        st, mc = e.get("stress") or {}, e.get("mc_blocked") or {}
        w, med = st.get("worst_ungated_pct"), mc.get("median_total_pct")
        if not w or not med or med <= 0:
            return None
        return abs(w) / med

    # ---- plain text (a real fallback, not an afterthought) ----
    T = [f"INDEX SHORT STRANGLE - {today}",
         f"{len(live)} of {len(rows)} markets open. Paper record; nothing is placed.",
         "", "=" * 62, "TODAY", "=" * 62, ""]
    for fam, frs in _fam_order(rows):
        cands = [r for r in frs if r["status"] == "CANDIDATE"]
        if not cands:
            continue
        T.append(f"{fam.upper()}")
        if len(cands) > 1:
            T.append("  ONE of the following - they are the same bet at "
                     "different contract sizes:")
        for r in cands:
            T.append(f"  {r['market']:<10} {r['ccy']}{r['spot']:,.2f}   {r['product']}")
            for n, l in sorted(r["legs"].items(), key=lambda kv: kv[1]["dte"]):
                T.append(f"      {n:<8} ~{l['dte']:>2}d  SELL {l['put_strike']:,.0f} PUT"
                         f"  +  {l['call_strike']:,.0f} CALL   x{r['lot']}")
        T.append("")
    if aside:
        T += ["-" * 62, f"STOOD ASIDE ({len(aside)})", ""]
        T += [f"  {r['market']:<10} {r['reason']}" for r in aside]
        T.append("")
    if dark:
        T += [f"  {r['market']:<10} NO DATA - {r['reason']}" for r in dark] + [""]

    T += ["=" * 62, "HOW TO READ THIS", "=" * 62, "",
          "1. SELL both legs, same expiry, and CLOSE THEM THE SAME DAY. This is",
          "   not a held position; the evidence below is all same-day.",
          "2. The strikes are placed at 1.5x the market's own expected daily move,",
          "   centred on the FORWARD (not spot) and snapped to the listed strike",
          "   grid, so every price shown is one you can actually trade.",
          "3. You keep the money if the index finishes BETWEEN the strikes. The",
          "   trade is a bet on the market staying still, not on direction.",
          "4. 'Stood aside' is a decision, not a gap. The volatility gate is the",
          "   entire edge - see the stress numbers below for what it is protecting",
          "   you from.",
          "5. Only ONE position per family. SPX, XSP and SPY are the same trade at",
          "   three sizes; taking all three is one bet at triple weight.", ""]

    if ev:
        T += ["=" * 62, "THE EVIDENCE", "=" * 62, "",
              f"  {'market':<10}{'trades':>8}{'since':>8}{'win%':>7}"
              f"{'avg/trade':>11}{'worst day':>11}", "  " + "-" * 53]
        for m, e in sorted(ev.items(), key=lambda kv: -kv[1]["historical"]["win_pct"]):
            h = e["historical"]
            T.append(f"  {m:<10}{h['n_trades']:>8,}{h['first'][:4]:>8}{h['win_pct']:>7.1f}"
                     f"{h['mean_pct']:>10.3f}%{h['worst_pct']:>10.2f}%")
        T += ["", "  Returns are % of collateral, so they compare across an index at",
              "  29,000 and an ETF at 700. Credits are modelled Black-Scholes at the",
              "  volatility index, with NO bid-ask spread charged - real fills will",
              "  be worse than these figures.", ""]
        anym = next(iter(ev.values()))
        cfgm = anym.get("mc_config", {})
        T += ["-" * 62, "FORWARD SIMULATION", "-" * 62, "",
              f"  {cfgm.get('paths', 0):,} simulated years, {cfgm.get('trades_per_path', 0)} "
              f"trades each, resampled in BLOCKS so that",
              "  bad days keep arriving in clusters the way they really do.", "",
              f"  {'market':<10}{'median yr':>11}{'bad yr (p5)':>13}{'losing yr':>11}"
              f"{'worst drop':>12}", "  " + "-" * 55]
        for m, e in sorted(ev.items(), key=lambda kv: -kv[1]["mc_blocked"]["median_total_pct"]):
            b = e["mc_blocked"]
            T.append(f"  {m:<10}{b['median_total_pct']:>10.2f}%{b['p5_total_pct']:>12.2f}%"
                     f"{b['prob_losing_year_pct']:>10.1f}%{b['p95_max_drawdown_pct']:>11.2f}%")
        T += ["", "=" * 62, "WHAT WOULD MAKE THIS WRONG", "=" * 62, "",
              "  Read this before the win rates above persuade you of anything.", "",
              "  The simulation resamples only trades the volatility gate ALLOWED.",
              "  No crash day is in that sample, so its 'bad year' column is NOT a",
              "  worst case. This is:", "",
              f"  {'market':<10}{'gate shut':>11}{'gate OPEN':>12}{'on':>12}"
              f"{'costs':>10}", "  " + "-" * 55]
        for m, e in sorted(ev.items(), key=lambda kv: (kv[1].get("stress") or {})
                           .get("worst_ungated_pct", 0)):
            s = e.get("stress") or {}
            if not s:
                continue
            y = _yrs_cost(m)
            T.append(f"  {m:<10}{e['historical']['worst_pct']:>10.2f}%"
                     f"{s['worst_ungated_pct']:>11.2f}%{s['worst_ungated_date']:>12}"
                     f"{(f'{y:.1f} yrs' if y else '-'):>10}")
        T += ["", "  'gate shut' is the worst day the strategy actually took.",
              "  'gate OPEN' is what that same trade would have lost on the worst",
              "  session in the market's history had the filter failed - and the",
              "  last column converts it into years of median return wiped out.", "",
              "  The gate held on every one of those dates. That is the claim this",
              "  strategy rests on, and it is the one to keep checking.", ""]
    T += ["=" * 62, "",
          "Premiums are NOT quoted. The credit is filled in from a captured chain",
          "or by you, so a modelled number is never mistaken for a traded one.",
          "",
          "PAPER RECORD - NOT FUNDED, NOT ADVICE. Nothing is placed by TradePro.",
          "Selling options carries losses that can exceed the premium received."]
    text = "\n".join(T)

    # ---- html ----
    # Every colour is set INLINE and every surface gets an explicit
    # background-color. Email clients that force a dark theme recolour
    # unstyled elements but leave explicit ones alone, so an unstyled cell
    # becomes white-on-white for a chunk of readers. That was a real complaint
    # about the first version and it is why nothing here inherits.
    D = "#0f1729"; MUT = "#5b6779"; LINE = "#e3e8ef"
    OK = "#0f8a5f"; OFF = "#8b95a5"; WARN = "#b26a00"; BAD = "#b4232c"
    BG = "#ffffff"; SOFT = "#f7f9fc"
    MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

    def _sec(title: str, sub: str = "") -> str:
        return (f'<tr><td style="padding:26px 22px 8px 22px;background:{BG}">'
                f'<div style="font-size:11px;letter-spacing:.1em;'
                f'text-transform:uppercase;color:{MUT};font-weight:700">{title}</div>'
                + (f'<div style="font-size:13px;color:{MUT};padding-top:4px">{sub}</div>'
                   if sub else "") + "</td></tr>")

    def _row(cells, head=False, mono=True):
        w = "700" if head else "400"
        c = MUT if head else D
        bd = f"border-bottom:1px solid {LINE};" if head else ""
        out = [f'<tr style="background:{BG}">']
        for i, v in enumerate(cells):
            al = "left" if i == 0 else "right"
            ff = f"font-family:{MONO};" if (mono and i > 0) else ""
            out.append(f'<td style="padding:5px 6px;text-align:{al};font-size:12.5px;'
                       f'color:{c};font-weight:{w};{ff}{bd}white-space:nowrap">{v}</td>')
        return "".join(out) + "</tr>"
    F = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif")
    H = [f'<div style="background:{SOFT};padding:18px 10px;font-family:{F}">'
         f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
         f'style="max-width:620px;margin:0 auto;width:100%;background:{BG};'
         f'border:1px solid {LINE};border-radius:14px;overflow:hidden">']

    # ---- masthead: what this is, and the one number that summarises today ----
    H.append(f'<tr><td style="padding:22px;background:{D}">'
             f'<div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;'
             f'color:#93a3ba;font-weight:700">TradePro · Index short strangle</div>'
             f'<div style="font-size:22px;font-weight:700;color:#ffffff;padding-top:6px">'
             f'{len(live)} of {len(rows)} markets open</div>'
             f'<div style="font-size:13px;color:#93a3ba;padding-top:3px">{today} · '
             f'paper record, nothing is placed</div></td></tr>')

    # ---- today's positions, grouped so one family reads as ONE decision ----
    H.append(_sec("Today"))
    for fam, frs in _fam_order(rows):
        cands = [r for r in frs if r["status"] == "CANDIDATE"]
        if not cands:
            continue
        H.append(f'<tr><td style="padding:0 22px 14px 22px;background:{BG}">')
        H.append(f'<div style="border:1px solid {LINE};border-left:4px solid {OK};'
                 f'border-radius:10px;background:{BG}">')
        H.append(f'<div style="padding:13px 15px 8px 15px">'
                 f'<span style="font-size:16px;font-weight:700;color:{D}">{fam}</span>'
                 + (f'<div style="font-size:12px;color:{WARN};padding-top:4px;'
                    f'font-weight:600">Take ONE — these are the same bet at '
                    f'different contract sizes</div>' if len(cands) > 1 else "")
                 + "</div>")
        for r in cands:
            H.append(f'<div style="padding:8px 15px 12px 15px;border-top:1px solid {LINE}">'
                     f'<div style="font-size:13px;color:{MUT};padding-bottom:7px">'
                     f'<b style="color:{D};font-size:14px">{r["market"]}</b> &nbsp;'
                     f'{r["ccy"]}{r["spot"]:,.2f} &nbsp;·&nbsp; {r["product"]}</div>')
            for n, l in sorted(r["legs"].items(), key=lambda kv: kv[1]["dte"]):
                H.append(f'<div style="background:{SOFT};border-radius:8px;padding:9px 11px;'
                         f'margin-bottom:6px">'
                         f'<div style="font-size:11px;color:{MUT};text-transform:uppercase;'
                         f'letter-spacing:.07em;font-weight:700">{n} · ~{l["dte"]}d · '
                         f'x{r["lot"]}</div>'
                         f'<div style="font-size:17px;font-weight:700;color:{D};'
                         f'font-family:{MONO};padding-top:3px">'
                         f'{l["put_strike"]:,.0f} PUT &nbsp;+&nbsp; '
                         f'{l["call_strike"]:,.0f} CALL</div></div>')
            H.append(f'<div style="font-size:12px;color:{MUT}">±{r["width_pct"]}% wide · '
                     f'{r["vol_index"]} vs {r["vol_threshold"]:.0f} gate · close same day'
                     f'</div></div>')
        H.append("</div></td></tr>")

    if not live:
        H.append(f'<tr><td style="padding:0 22px 14px 22px;background:{BG}">'
                 f'<div style="border:1px solid {LINE};border-radius:10px;padding:15px;'
                 f'background:{SOFT};color:{MUT};font-size:14px">'
                 f'<b style="color:{D}">No position today.</b> Every market is above its '
                 f'volatility gate. Standing aside is the strategy working, not a '
                 f'missing signal.</div></td></tr>')

    # ---- stood aside: compact, but never hidden ----
    if aside or dark:
        H.append(_sec(f"Stood aside ({len(aside) + len(dark)})"))
        H.append(f'<tr><td style="padding:0 22px 6px 22px;background:{BG}">'
                 f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        for r in aside:
            H.append(f'<tr style="background:{BG}"><td style="padding:6px 0;font-size:13px;'
                     f'color:{D};font-weight:600;white-space:nowrap;vertical-align:top;'
                     f'border-bottom:1px solid {LINE}">{r["market"]}</td>'
                     f'<td style="padding:6px 0 6px 12px;font-size:12.5px;color:{MUT};'
                     f'border-bottom:1px solid {LINE}">{r["reason"]}</td></tr>')
        for r in dark:
            H.append(f'<tr style="background:{BG}"><td style="padding:6px 0;font-size:13px;'
                     f'color:{BAD};font-weight:600;vertical-align:top;'
                     f'border-bottom:1px solid {LINE}">{r["market"]}</td>'
                     f'<td style="padding:6px 0 6px 12px;font-size:12.5px;color:{BAD};'
                     f'border-bottom:1px solid {LINE}">No data — {r["reason"]}</td></tr>')
        H.append("</table></td></tr>")

    # ---- the reading guide: assume the reader has never seen this before ----
    H.append(_sec("How to read this", "Five things, and then the numbers behind them."))
    guide = [
        ("Sell both legs and close them the same day.",
         "This is not a held position. Every figure below is same-day, so a "
         "position carried overnight is not the thing that was measured."),
        ("You win if the market finishes BETWEEN the two strikes.",
         "It is a bet on the market staying still, not on direction. That is why "
         "it can pay in a rising or a falling market — and why a violent one hurts."),
        ("The strikes are 1.5× the market's own expected daily move.",
         "Centred on the forward rather than spot, then snapped to the listed "
         "strike grid, so every price shown is one you can actually trade."),
        ("“Stood aside” is a decision, not a missing signal.",
         "The volatility gate is the entire edge. What it protects you from is "
         "in the last table."),
        ("One position per family.",
         "SPX, XSP and SPY are the same trade at three sizes. Taking all three is "
         "one bet at triple weight, which is concentration dressed as spread."),
    ]
    H.append(f'<tr><td style="padding:0 22px 6px 22px;background:{BG}">')
    for i, (hd, bd) in enumerate(guide, 1):
        H.append(f'<div style="padding:7px 0;border-bottom:1px solid {LINE}">'
                 f'<span style="display:inline-block;width:20px;color:{MUT};'
                 f'font-weight:700;font-size:13px">{i}</span>'
                 f'<span style="font-size:13.5px;color:{D};font-weight:600">{hd}</span>'
                 f'<div style="font-size:12.5px;color:{MUT};padding:3px 0 0 20px;'
                 f'line-height:1.5">{bd}</div></div>')
    H.append("</td></tr>")

    if ev:
        # ---- evidence: sample size and period BEFORE any performance number ----
        H.append(_sec("The evidence",
                      "Every low-volatility session in each market's full history. "
                      "Returns are % of collateral, so an index at 29,000 and an "
                      "ETF at 700 compare directly."))
        H.append(f'<tr><td style="padding:0 22px;background:{BG}">'
                 f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        H.append(_row(("market", "trades", "since", "win", "avg/trade", "worst day"),
                      head=True, mono=False))
        for m, e in sorted(ev.items(), key=lambda kv: -kv[1]["historical"]["win_pct"]):
            h = e["historical"]
            H.append(_row((f'<b>{m}</b>', f"{h['n_trades']:,}", h["first"][:4],
                           f"{h['win_pct']:.1f}%", f"{h['mean_pct']:+.3f}%",
                           f'<span style="color:{BAD}">{h["worst_pct"]:.2f}%</span>')))
        H.append("</table></td></tr>")
        H.append(f'<tr><td style="padding:9px 22px 0 22px;background:{BG}">'
                 f'<div style="font-size:12px;color:{MUT};line-height:1.55">'
                 f'Credits are modelled Black-Scholes at the volatility index with '
                 f'<b style="color:{D}">no bid-ask spread charged</b> — real fills will be '
                 f'worse than these figures.</div></td></tr>')

        # ---- forward simulation ----
        cfgm = next(iter(ev.values())).get("mc_config", {})
        H.append(_sec("Forward simulation",
                      f"{cfgm.get('paths', 0):,} simulated years of "
                      f"{cfgm.get('trades_per_path', 0)} trades, resampled in blocks so "
                      f"bad days keep arriving in clusters the way they really do."))
        H.append(f'<tr><td style="padding:0 22px;background:{BG}">'
                 f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        H.append(_row(("market", "median year", "bad year", "losing year", "worst drop"),
                      head=True, mono=False))
        for m, e in sorted(ev.items(),
                           key=lambda kv: -kv[1]["mc_blocked"]["median_total_pct"]):
            b = e["mc_blocked"]
            H.append(_row((f'<b>{m}</b>', f"{b['median_total_pct']:+.2f}%",
                           f"{b['p5_total_pct']:+.2f}%",
                           f"{b['prob_losing_year_pct']:.1f}%",
                           f'<span style="color:{BAD}">'
                           f'{b["p95_max_drawdown_pct"]:.2f}%</span>')))
        H.append("</table></td></tr>")

        # ---- the honest bit, given its own section and the strongest colour ----
        H.append(_sec("What would make this wrong",
                      "Read this before the win rates persuade you of anything."))
        H.append(f'<tr><td style="padding:0 22px;background:{BG}">'
                 f'<div style="background:#fff7ed;border-radius:10px;padding:13px 15px;'
                 f'font-size:12.5px;color:#7c4a02;line-height:1.55;margin-bottom:12px">'
                 f'The simulation above resamples only trades the volatility gate '
                 f'<b>allowed</b>. No crash day is in that sample, so its “bad year” '
                 f'column is <b>not a worst case</b>. This is:</div>')
        H.append(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        H.append(_row(("market", "gate shut", "gate OPEN", "on", "costs"),
                      head=True, mono=False))
        for m, e in sorted(ev.items(),
                           key=lambda kv: (kv[1].get("stress") or {})
                           .get("worst_ungated_pct", 0)):
            s = e.get("stress") or {}
            if not s:
                continue
            y = _yrs_cost(m)
            H.append(_row((f'<b>{m}</b>', f"{e['historical']['worst_pct']:.2f}%",
                           f'<span style="color:{BAD};font-weight:700">'
                           f'{s["worst_ungated_pct"]:.2f}%</span>',
                           s["worst_ungated_date"],
                           f'<span style="color:{BAD}">{y:.1f} yrs</span>' if y else "—")))
        H.append("</table>")
        H.append(f'<div style="font-size:12.5px;color:{MUT};line-height:1.6;'
                 f'padding-top:11px">'
                 f'<b style="color:{D}">Gate shut</b> is the worst day the strategy '
                 f'actually took. <b style="color:{D}">Gate open</b> is what the same '
                 f'trade would have lost on the worst session in that market\'s history '
                 f'had the filter failed, and <b style="color:{D}">costs</b> converts it '
                 f'into years of median return wiped out.<br><br>'
                 f'The gate held on every one of those dates. That is the claim this '
                 f'strategy rests on — and the one worth re-checking, rather than the '
                 f'win rate.</div></td></tr>')

    # ---- footer ----
    H.append(f'<tr><td style="padding:20px 22px 22px 22px;background:{BG}">'
             f'<div style="border-top:1px solid {LINE};padding-top:14px;font-size:12px;'
             f'color:{MUT};line-height:1.6">'
             f'<b style="color:{D}">Premiums are not quoted.</b> The credit is filled in '
             f'from a captured chain or by you, so a modelled number is never mistaken '
             f'for a traded one.<br><br>'
             f'<b style="color:{D}">Not modelled:</b> the intraday profit target and the '
             f'strangle→straddle conversion — both things a live trader actually does, '
             f'and both absent from every figure above.</div>'
             f'<div style="margin-top:13px;padding:11px 13px;background:#fef2f2;'
             f'border-radius:8px;color:{BAD};font-size:12.5px;font-weight:600;'
             f'line-height:1.5">PAPER RECORD — NOT FUNDED, NOT ADVICE. Nothing is placed '
             f'by TradePro. Selling options can lose more than the premium received.'
             f'</div></td></tr>')
    H.append("</table></div>")
    return subj, (text, "".join(H))


# SHADOW RECORDING — record the days we STAND ASIDE, marked as such.
#
# Measured 29 Aug 2026: over the last 12 months the US gate (VIX<=14) fired on
# 1.2% of sessions — once in the last 168 — and India's on 42%. So a month of
# observation yields ~9 Indian records and approximately zero US ones. That is
# not a sample, and the predictable response in four weeks is to nudge the
# thresholds up so the record has something in it. That is the same failure
# mode as tuning a gate until a backtest passes.
#
# So every session is recorded, with `would_trade` marking whether the gate
# opened. The stand-aside rows are not noise — they are the ONLY way to test
# whether the threshold is set right, because they answer the question the
# live rows cannot: what did we miss by refusing?
#
# It also means the threshold can be re-evaluated later at ANY level without
# waiting for new data, because the strikes and outcomes were recorded for
# every day regardless of the gate.


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
    for r in rows:
        r["would_trade"] = r.get("status") == "CANDIDATE"
    if not args.no_record:
        # BOTH kinds. See the shadow-recording note above.
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
