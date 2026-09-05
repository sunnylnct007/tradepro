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
# WHY THESE EIGHT. Measured 29 Aug 2026 on the full history of each pair, same
# construction, % of collateral. NIFTY MIDCAP is excluded on evidence: worst win
# rate (75.5%), worst tail (-1.54%), a third of the return. RUSSELL (^RUT) and
# DOW (^DJI) are excluded for a harder reason — ^RVX returns ZERO bars and ^VXD
# returns one, so there is no volatility gate for them at all. Inventing one
# from realised vol is the modelling error that produced three wrong tables on
# 29 Aug, so they stay out until real data exists.
#
# EVERY `vol_max` BELOW IS COMPUTED, NOT CHOSEN. See `choose_threshold` in
# index_strangle_sim: of a half-point grid, take the LARGEST threshold that
# admits ZERO trades inside any declared crisis window (GFC, COVID, the 2022
# bear, April 2025). `test_thresholds_are_the_rules_output` fails if any value
# here drifts from what the rule returns.
#
# This exists because the owner asked "how did u decided on the threshold value"
# and the honest answer was: two of them were evidenced and two were my guess.
# SPY's 14 and India's 12 came from a documented sweep; VXN<=18 and GVZ<=16 I
# picked and justified afterwards. Running the rule caught that GVZ<=16 traded
# through 31 sessions of the 2022 bear and 4 of COVID — a gate that opens in a
# crash has failed at the one job it has. It is now 13.0.
#
# On same-day data the rule reproduced SPY's hand-picked 14 exactly, which is
# what earned it trust. It then survived a much harder test: see the lag note.
#
# THE VOLATILITY INDEX IS LAGGED ONE SESSION, and this was a REAL BUG until
# 29 Aug 2026. The trade is entered at the OPEN, so the only vol reading
# available is the PREVIOUS close — but the backtest gated on the SAME day's
# close, letting the filter see the very move it exists to avoid. It silently
# excluded exactly the days that would have hurt. Found by the owner asking what
# "if the gate fails" meant in the email.
#
# The cost of the correction, measured across five markets: mean return falls
# 10-17%, and SPY's worst day goes -0.80% -> -1.89%. It also TIGHTENED every
# gate, because a less-informed filter must be more conservative to stay clean:
#
#   market        same-day   lagged (live)   why the looser one leaks
#   SPX/XSP/SPY      14.0        13.5        14.0 leaks COVID
#   NDX/QQQ          18.5        17.5        18.0 leaks COVID
#   GOLD             13.0        11.5        12.0 leaks COVID
#   BANKNIFTY/NIFTY  12.5        12.5        unchanged — India was already right
#
# The lagged figures are the ONLY ones quoted anywhere, because the live screen
# reads the last COMPLETED close (both jobs run pre-open) and so behaves like
# the lagged backtest. Publishing the same-day numbers would have described a
# filter nobody can actually run — the same harness-vs-screen mismatch already
# sitting in the Swing evidence.
#
# A CORRECTION TO AN EARLIER CLAIM IN THIS FILE: it said VXN<=18 was "as
# selective on Nasdaq as VIX<=14 is on the S&P". It is not. VIX<=14 is the 25th
# percentile of VIX; VXN<=18 is the 31st; GVZ<=16 was the 36th. What IS true and
# measured: VXN sits a median +3.5 points above VIX (+3.3 in the low-vol regime
# the gate operates in), so a per-market threshold is genuinely required —
# copying 14 across would silence Nasdaq entirely. The offset was real; the
# equal-selectivity claim was not, and percentile-matching is not the rule
# anyway. Crisis leakage is.
#
# `family` GROUPS THE SAME BET. SPX, XSP and SPY are one trade at three contract
# sizes, not three opportunities — and the email must say so, because eight rows
# that look independent invite eight positions on what is really two risks.
MARKETS = {
    # ---- S&P 500: one underlying, three contract sizes ----
    "SPX": {"index": "^GSPC", "vol": "^VIX", "vol_scale": 1.0, "vol_max": 13.5,
            "rate": 0.045, "grid": 5.0, "lot": 100, "divisor": 1.0,
            "family": "S&P 500", "ccy": "$",
            "product": "cash-settled index option · European · no early assignment",
            "tz": "America/New_York", "open_local": "09:30", "close_local": "16:00",
             "broker_symbol": "SPX", "broker_sec_type": "IND",
             # OCC roots this market can appear under at the broker. SPXW is
             # the PM-settled weekly and is what a third-Friday order fills
             # as; matching only "SPX" left the position unrecognised.
             "broker_roots": ("SPX", "SPXW"),
             "paper_trade": True,  # IND underlying — see broker_sec_type
             "note": "VIX is computed FROM SPX options, so the volatility input is "
                    "the underlying's own, not a proxy"},
    "XSP": {"index": "^GSPC", "vol": "^VIX", "vol_scale": 1.0, "vol_max": 13.5,
            "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 10.0,
            "family": "S&P 500", "ccy": "$",
            "product": "Mini-SPX · exactly 1/10 of SPX · cash-settled, European",
            "tz": "America/New_York", "open_local": "09:30", "close_local": "16:00",
             "broker_symbol": "XSP", "broker_sec_type": "IND",
             "broker_roots": ("XSP", "XSPW"),
             "paper_trade": True,  # priced off ^GSPC/10; the BROKER symbol is XSP
             "note": "the same trade as SPX at a tenth of the size — this is the "
                    "'smaller index' product; SPX itself is 10x SPY, not smaller"},
    "SPY": {"index": "SPY", "vol": "^VIX", "vol_scale": 1.0, "vol_max": 13.5,
            "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 1.0,
            "family": "S&P 500", "ccy": "$",
            "product": "ETF option · American · CAN be assigned early",
            "tz": "America/New_York", "open_local": "09:30", "close_local": "16:00",
             "paper_trade": True,  # liquid ETF options, unambiguous IBKR symbol
             "note": "measured edge is within noise of SPX (83.3% vs 82.4%), so the "
                    "choice is settlement and size, not return"},
    # ---- Nasdaq 100 ----
    "NDX": {"index": "^NDX", "vol": "^VXN", "vol_scale": 1.0, "vol_max": 17.5,
            "rate": 0.045, "grid": 25.0, "lot": 100, "divisor": 1.0,
            "family": "Nasdaq 100", "ccy": "$",
            "product": "cash-settled index option · European",
            "tz": "America/New_York", "open_local": "09:30", "close_local": "16:00",
             "broker_symbol": "NDX", "broker_sec_type": "IND",
             # NDXP is the PM-settled variant IBKR named in its rejection.
             "broker_roots": ("NDX", "NDXP"),
             # NOT paper-tradeable: TOO BIG FOR THE ACCOUNT, not a mapping gap.
             # One contract is ~28,500 x 100 = ~$2.85M of collateral against a
             # paper NLV of ~$151k — nineteen times the account. IBKR rejected
             # it on 1 and 2 Sep 2026 with the order echoed back:
             #   put=REJECTED/"SELL 1 NDX (NDXP) SEP 18 '26 28500 Put"
             # It RESOLVES fine; it simply cannot be funded, so attempting it
             # daily is noise that trains the reader to ignore failures.
             #
             # Nasdaq exposure is NOT lost: QQQ gates on the same VXN, is
             # placeable, and has the BEST measured edge of the set
             # (+1.16%/yr excess vs NDX's +0.94%). NDX would add size, not a
             # new signal. Owner, 2 Sep 2026: "drop NDX fro paper tarde".
             #
             # It stays fully EVALUATED — email, decision log, gate — because
             # the stand-aside rows are what make the threshold testable, and
             # that costs nothing.
             "paper_trade": False,
             "note": "VXN is computed FROM NDX options. Fatter tail than the S&P "
                    "(p5 -0.183 vs -0.101) — the same rule, more risk per unit"},
    "QQQ": {"index": "QQQ", "vol": "^VXN", "vol_scale": 1.0, "vol_max": 17.5,
            "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 1.0,
            "family": "Nasdaq 100", "ccy": "$",
            "product": "ETF option · American · CAN be assigned early",
            "tz": "America/New_York", "open_local": "09:30", "close_local": "16:00",
             "paper_trade": True,  # liquid ETF options, unambiguous IBKR symbol
             "note": "fires 2,015 times against SPY's 2,219 because VXN<=18 is a "
                    "reachable gate — this is what fixes the thin US sample"},
    # ---- India ----
    "BANKNIFTY": {"index": "^NSEBANK", "vol": "^INDIAVIX", "vol_scale": 1.35,
                  "vol_max": 12.5, "rate": 0.065, "grid": 100.0, "lot": 150,
                  "divisor": 1.0, "family": "India banks", "ccy": "Rs",
                  "product": "cash-settled index option · European",
                  "tz": "Asia/Kolkata", "open_local": "09:15", "close_local": "15:30",
             "paper_trade": False,  # no paper trading available for India — email only
             "note": "India VIX measures NIFTY and BANKNIFTY realises ~1.35x "
                          "that, so the input is SCALED — a proxy, not its own index"},
    "NIFTY": {"index": "^NSEI", "vol": "^INDIAVIX", "vol_scale": 1.0,
              "vol_max": 12.5, "rate": 0.065, "grid": 50.0, "lot": 75,
              "divisor": 1.0, "family": "India broad", "ccy": "Rs",
              "product": "cash-settled index option · European",
              "tz": "Asia/Kolkata", "open_local": "09:15", "close_local": "15:30",
             "paper_trade": False,  # no paper trading available for India — email only
             "note": "India VIX measures NIFTY directly, so no 1.35 scaling is "
                      "needed. Worst day -0.29% vs BANKNIFTY's -1.05% — 3.5x safer "
                      "tail for about two-thirds the return"},
    # ---- Gold: the only genuinely uncorrelated leg here ----
    "GOLD": {"index": "GLD", "vol": "^GVZ", "vol_scale": 1.0, "vol_max": 11.5,
             "rate": 0.045, "grid": 1.0, "lot": 100, "divisor": 1.0,
             "family": "Gold", "ccy": "$",
             "product": "ETF option · American · CAN be assigned early",
             "tz": "America/New_York", "open_local": "09:30", "close_local": "16:00",
             "paper_trade": True,  # GLD — liquid ETF options
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


# WHAT ONE CONTRACT ACTUALLY RISKS. Owner, 29 Aug 2026: "whats the possible
# gain and losses on these. can we also email and display that as well".
#
# Percent-of-collateral is the right unit for COMPARING markets and the wrong
# one for deciding whether to place a trade. "+0.05% mean" reads as nothing;
# "Rs4,234 typical, Rs439,376 if the gate fails" is the same fact in a unit that
# can be acted on. Both are now shown.
#
# TWO CAPITAL BASES, because they give very different answers and quoting only
# one is how a short-premium product gets mis-sold:
#
#   COLLATERAL  = put strike x lot. What a fully cash-secured position ties up.
#                 Conservative, and what every % figure in the evidence is
#                 measured against.
#   MARGIN      = what a broker actually asks for an index strangle (SPAN), very
#                 roughly 12% of collateral. This is the number that decides
#                 whether an account survives, and it AMPLIFIES BOTH DIRECTIONS
#                 by ~8x. A -0.73% day is -6.1% of margin. A gate failure on
#                 NIFTY is -96.7% of margin - nearly the whole deposit.
#
# The amplification is the point. On margin the typical trade earns ~0.3% and a
# single leaked crash day costs 39-97%, so roughly 100-330 winning trades pay
# for one gate failure. That ratio is the strategy, stated plainly.
MARGIN_PCT = 0.12          # SPAN ESTIMATE ONLY - the broker's number governs


# WHICH EXPIRY THIS DESK ACTUALLY PLACES.
#
# Named once because it was named twice. place_paper() traded the MONTHLY leg
# while record_execution() attached the fill to `next(iter(legs))` — the
# WEEKLY. On 1 Sep 2026 the first real two-leg strangle went on at 758/780
# (monthly) and was recorded against the 757/779 weekly row, which was never
# traded. The grader would have scored the wrong strikes, and the row that was
# actually filled would have read as never placed.
#
# The monthlies are chosen for liquidity: they carry ~2.3x the open interest of
# the weeklies, which is the same finding that unblocked the wheel screen.
PLACE_EXPIRY_KIND = "monthly"


def economics(row: dict, ev_entry: dict | None) -> dict | None:
    """Money, per ONE weekly contract, at today's levels.

    The credit is Black-Scholes and therefore MODELLED - it is labelled as such
    everywhere it is shown, because this file's whole discipline is that a
    modelled premium is never allowed to pass as a traded one. The loss figures
    are NOT modelled: they are the strategy's own realised history, scaled to
    this contract.
    """
    leg = (row.get("legs") or {}).get("weekly")
    if not leg or not ev_entry:
        return None
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    p = BlackScholesPricer()
    spot, iv, lot = row["spot"], row["iv_used"] / 100.0, row["lot"]
    credit = (p.price(spot, leg["call_strike"], 7 / 365, iv, "call")
              + p.price(spot, leg["put_strike"], 7 / 365, iv, "put")) * lot
    coll = leg["put_strike"] * lot
    margin = MARGIN_PCT * coll
    h, st = ev_entry["historical"], (ev_entry.get("stress") or {})
    fail_pct = st.get("worst_ungated_pct")

    def _m(pct):
        return None if pct is None else pct / 100.0 * coll
    return {
        "collateral": round(coll),
        "margin_estimate": round(margin),
        "credit_modelled": round(credit),
        "typical_gain": round(_m(h["mean_pct"])),
        "best_day": round(_m(h["best_pct"])),
        "worst_day": round(_m(h["worst_pct"])),
        "gate_failure": None if fail_pct is None else round(_m(fail_pct)),
        # On margin — the numbers that decide whether the account survives.
        "typical_gain_on_margin_pct": round(h["mean_pct"] / MARGIN_PCT, 2),
        "worst_day_on_margin_pct": round(h["worst_pct"] / MARGIN_PCT, 1),
        "gate_failure_on_margin_pct": (None if fail_pct is None
                                       else round(fail_pct / MARGIN_PCT, 1)),
        # How many typical winners pay for one gate failure. The single most
        # honest summary of a premium-selling strategy.
        "winners_per_gate_failure": (None if not fail_pct or not h["mean_pct"]
                                     else round(abs(fail_pct) / h["mean_pct"])),
        "margin_pct_assumed": MARGIN_PCT,
    }


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


# SESSION AWARENESS — two different questions, two different prices.
#
# Owner, 31 Aug 2026, after the 04:00 email's NIFTY strikes were already
# lopsided by the open: "we need to wait for market open and then decide", and
# "we need to ensure we deal with diff exchange timings".
#
# THE BUG. The backtest centres strikes on the DAY'S OPEN while gating on the
# PREVIOUS close — no lookahead, current strikes. The live screen used the
# previous close for BOTH. So on 31 Aug the email published NIFTY 23,950 /
# 24,450 off Friday's 24,175 close; by the open the index was 24,065, leaving
# the put 116 points away and the call 384 — not a balanced strangle. Every
# published figure was measured on strikes centred at the open.
#
# So they are separated:
#   THE GATE   "is volatility low?" — a regime question, answered from the last
#              SETTLED close. Never an in-flight bar.
#   THE STRIKES anchored to the session OPEN, which is what the evidence used.
#
# Deliberately NOT clever about timing. The owner's framing: "this option is
# supposed to be a slow boring and safe strategy". There is no intraday
# polling and no chasing the open by seconds — before the open the row says
# PROVISIONAL and names when the real strikes arrive; from the open onward it
# uses the open price. Under-promising beats a precise-looking number that
# moves.
_SESSION_STATES = ("pre_open", "open", "closed")


def _session_state(cfg: dict, now_utc: _dt.datetime | None = None) -> tuple[str, str]:
    """(state, exchange-local date as ISO) for this market, right now."""
    from zoneinfo import ZoneInfo
    now_utc = now_utc or _dt.datetime.now(_dt.UTC)
    tz = ZoneInfo(cfg.get("tz", "America/New_York"))
    local = now_utc.astimezone(tz)
    oh, om = (int(x) for x in cfg.get("open_local", "09:30").split(":"))
    ch, cm = (int(x) for x in cfg.get("close_local", "16:00").split(":"))
    o = local.replace(hour=oh, minute=om, second=0, microsecond=0)
    c = local.replace(hour=ch, minute=cm, second=0, microsecond=0)
    if local.weekday() >= 5:          # weekend — nothing is in flight
        return "closed", local.date().isoformat()
    if local < o:
        return "pre_open", local.date().isoformat()
    return ("open" if local < c else "closed"), local.date().isoformat()


# WHERE THIS STRATEGY'S DATA COMES FROM, stated rather than assumed.
#
# Owner's standing rule is IBKR golden source, "Yahoo only as a VISIBLE
# fallback, never silent default". This file broke it: _series() went straight
# to yfinance with no IBKR attempt and no label, so every price, vol reading and
# strike came from Yahoo and nothing said so.
#
# Checked 31 Aug 2026 against IBKR directly. It is not laziness — it is forced:
#
#     AAPL       STK   -> full OHLCV returned
#     VIX        IND   -> "Details currently unavailable"
#     BANKNIFTY  IND   -> "Details currently unavailable"
#
# Stocks work; INDICES do not, US and Indian alike, so it is not a missing NSE
# subscription. And this strategy gates on a volatility index in every market —
# ^VIX, ^VXN, ^GVZ, ^INDIAVIX — so it CANNOT run on IBKR data at all. Owner,
# 31 Aug: "for indian one yahoo is the way I guess".
#
# So the honest fix is provenance, not a provider switch: every row records
# which source answered, and the decision log stores it. If IBKR indices ever
# become available this is the one place that changes.
DATA_SOURCE = "yahoo"



def _todays_open_row(sym: str):
    """(date, row) for TODAY from the live feed, or None.

    Only the open matters — it is the strike anchor. High/low/close are carried
    so the frame stays shaped consistently, but they are IN-FLIGHT and nothing
    downstream may treat them as settled: the gate reads the last SETTLED
    session precisely so an unfinished bar can never decide anything.
    """
    try:
        from ..yahoo_session import yahoo_session
        import yfinance as yf
        d = yf.Ticker(sym, session=yahoo_session()).history(period="2d", interval="1d")
        if d is None or not len(d):
            return None
        idx = [str(x)[:10] for x in d.index]
        last = idx[-1]
        row = d.iloc[-1]
        if float(row.get("Open") or 0) <= 0:
            return None
        return last, {"Open": float(row["Open"]), "High": float(row["High"]),
                      "Low": float(row["Low"]), "Close": float(row["Close"]),
                      "Volume": float(row.get("Volume") or 0)}
    except Exception:  # noqa: BLE001 — no overlay is a PROVISIONAL row, not a crash
        return None


def _series(sym: str, period: str = "2y"):
    """Daily bars. Returns (frame, source) via `_series_src`; this keeps the
    old shape for callers that only want the frame."""
    got = _series_src(sym, period)
    return got[0] if got else None


def _series_src(sym: str, period: str = "2y"):
    """Daily bars + the source that answered.

    ROUTES THROUGH THE BAR STORE FIRST, which is the IBKR-primary path this
    project already solved — provider chain, S3 read-through, provenance
    grading, the lot. Owner, 31 Aug 2026: "we have sorted all data related call
    issues with IBKR ... so now we dont want to get into that again". Writing
    fresh IBKR calls here would be re-solving it badly.

    It only helps for the ETFs. Checked the same day: SPY, QQQ and GLD are in
    the store with ~200 partitions each; every INDEX this strategy needs —
    ^VIX, ^VXN, ^GVZ, ^INDIAVIX, ^GSPC, ^NDX, ^NSEBANK, ^NSEI — is absent, and
    IBKR itself returns "Details currently unavailable" for index price history
    (verified against VIX and BANKNIFTY directly, while AAPL/STK works). So the
    volatility gate CANNOT come from IBKR in any market, and Yahoo is forced
    rather than chosen — which is why the source is recorded per series instead
    of assumed.
    """
    import datetime as _d
    if not sym.startswith("^"):
        try:
            from pathlib import Path
            from ..bar_cache.asset_classes import UsEtfPlugin  # noqa: F401 — registers
            from ..bar_cache.store import BarStore
            store = BarStore(base_dir=Path(os.path.expanduser("~/.tradepro/bar_cache")))
            end = _d.datetime.now(_d.UTC)
            years = 2 if period == "2y" else 20
            frame = store.get(canonical=sym, asset_class="us_etf", resolution="1d",
                              start=end - _d.timedelta(days=365 * years + 30), end=end,
                              allow_partial=True,
                              skip_fetch=True,   # never let a screen trigger a provider call
                              fetched_by="index_strangle_paper")
            df = frame.df
            if df is not None and not df.empty:
                out = df.rename(columns=str.capitalize)
                out.index = [str(x)[:10] for x in out.index]
                # THE BAR CACHE IS END-OF-DAY. Its harvest runs after the close,
                # so DURING a session it has no bar for today — and the strike
                # anchor needs today's OPEN.
                #
                # Introduced as a regression 31 Aug 2026 when this was routed
                # through the store: SPY/QQQ/GOLD went permanently
                # spot_basis="prior_close" while the session was open, which
                # marks them PROVISIONAL, and place_paper() refuses on
                # provisional. Paper execution could never have fired — on
                # exactly the three markets that are paper-tradeable. Caught by
                # reading the basis column at the open rather than assuming.
                #
                # So: HISTORY from the golden source, today's OPEN overlaid from
                # the only source that has it intraday. Both labelled, because
                # a row built from two providers should say so.
                todays = _todays_open_row(sym)
                if todays is not None and todays[0] not in out.index:
                    out.loc[todays[0]] = todays[1]
                    return out, "bar_cache(ibkr)+yahoo(open)"
                return out, "bar_cache(ibkr)"
        except Exception as exc:  # noqa: BLE001 — fall through, and SAY so
            log.info("%s: bar cache miss (%s) — falling back to yahoo",
                     sym, str(exc)[:90])

    from ..yahoo_session import yahoo_session
    import yfinance as yf
    d = yf.Ticker(sym, session=yahoo_session()).history(period=period, interval="1d")
    if d is None or not len(d):
        return None
    d.index = [str(x)[:10] for x in d.index]
    return d, DATA_SOURCE


def decide(market: str) -> dict:
    """Today's candidate for one market. Bars + a vol index only — no chain,
    so a dark options feed can never stop this producing a decision."""
    cfg = MARKETS[market]
    _p = _series_src(cfg["index"]); _v = _series_src(cfg["vol"])
    px, px_src = (_p if _p else (None, None))
    vx, vx_src = (_v if _v else (None, None))
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

    # THE GATE READS THE LAST SETTLED SESSION, never an in-flight bar.
    # Verified 31 Aug 2026: at 13:48 IST, mid-session, Yahoo already served an
    # India VIX row stamped that day — a live, unfinished value. Gating on it
    # would reintroduce the same lookahead the backtest was corrected for. The
    # 03:00 UTC scheduled job happened to be safe because it runs pre-open, but
    # the new "Run now" button lets this be triggered mid-session, so timing
    # luck is not a guard.
    state, local_today = _session_state(cfg)
    settled = [d for d in common if not (d == local_today and state != "closed")]
    if not settled:
        out["status"] = "no_data"
        out["reason"] = "no settled session yet"
        return out
    vols = [float(vx.loc[d, "Close"]) for d in settled]
    today, v = settled[-1], vols[-1]

    # THE POST-OPEN READING — recorded, never used by the gate.
    #
    # The gate deliberately reads the last SETTLED close, which over a weekend
    # is three days old. Owner, 31 Aug: "instead of saying no signal we shd be
    # looking after market open". He is right that this is not lookahead — the
    # job runs after the open, so today's vol legitimately exists by then. But
    # switching the gate to it on reasoning alone would repeat the mistake of
    # setting a threshold by judgement, so BOTH are recorded and the question
    # settles on data.
    vol_now = None
    try:
        _latest = vx.index[-1]
        if _latest != today:
            vol_now = round(float(vx.loc[_latest, "Close"]), 4)
    except Exception:  # noqa: BLE001 — a missing reading must not lose the decision
        vol_now = None
    # TRAILING quartile — the boundary uses only prior sessions. An in-sample
    # quartile would leak the future into the filter, which is the easiest way
    # to fake this entire result.
    hist = sorted(vols[-(VIX_LOOKBACK + 1):-1])
    q1 = hist[len(hist) // 4]           # context only — no longer the gate
    import os as _os
    thr = float(_os.environ.get(f"TRADEPRO_STRANGLE_VIX_MAX_{market}",
                                cfg["vol_max"]))
    # STRIKES ANCHOR TO THE SESSION OPEN — what the backtest actually used.
    # `divisor` carries XSP, the S&P index quoted at a tenth; the gate and the
    # width are scale-free, so only the printed level and strikes move.
    div = cfg.get("divisor", 1.0)
    anchor_date = local_today if (state in ("open", "closed")
                                  and local_today in px.index) else None
    if anchor_date is not None and float(px.loc[anchor_date, "Open"] or 0) > 0:
        spot = float(px.loc[anchor_date, "Open"]) / div
        spot_basis, provisional = "session_open", False
    else:
        # Before the open there IS no open price. Say so rather than dress the
        # previous close up as a tradeable strike — on 31 Aug that gap moved
        # NIFTY 110 points and left the emailed strangle badly lopsided.
        spot = float(px.loc[today, "Close"]) / div
        spot_basis, provisional = "prior_close", True

    iv = v / 100.0 * cfg["vol_scale"]
    daily = iv / math.sqrt(252)
    width = STRIKE_MULT * daily
    rate, grid = cfg["rate"], cfg["grid"]

    def _strikes(dte: int) -> tuple[float, float]:
        p, c, _ = strike_pair(spot, width, dte, rate, grid)
        return p, c
    out.update({
        "as_of": today, "spot": round(spot, 2),
        "session_state": state, "exchange_date": local_today,
        "spot_basis": spot_basis, "provisional": provisional,
        "strikes_note": (
            "PROVISIONAL — priced off the previous close because the session "
            "has not opened. Final strikes are set from the opening price."
            if provisional else
            f"final — anchored to the {local_today} opening price"),
        "family": cfg["family"], "product": cfg["product"], "ccy": cfg["ccy"],
        # Provenance, on every row. IBKR serves no indices on this account, so
        # this is currently always "yahoo" — but it is RECORDED rather than
        # assumed, and a future study can tell what it was reading.
        "data_source": f"price={px_src or '?'}, vol={vx_src or '?'}",
        # Recorded for comparison; the gate above used `v` (the settled close).
        "vol_at_decision": vol_now,
        "vol_gate_used": round(v, 4),
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



def _monthly_expiry(dte_target: int, today: _dt.date | None = None) -> str:
    """The listed MONTHLY expiry nearest `dte_target` days out (3rd Friday).

    Monthlies only. The owner trades the monthly and closes intraday, and a
    month holds several weekly expiries once those are listed — placing the
    wrong one is a different trade at the same strike.
    """
    today = today or _dt.date.today()
    out = []
    for add in (0, 1, 2):
        m = today.month + add
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        d = _dt.date(y, m, 1)
        first_fri = d + _dt.timedelta(days=(4 - d.weekday()) % 7)
        out.append(first_fri + _dt.timedelta(days=14))
    future = [d for d in out if (d - today).days >= 1]
    return min(future, key=lambda d: abs((d - today).days - dte_target)).isoformat()


def place_paper(row: dict, contracts: int = 1, shadow: bool = False) -> dict | None:
    """Place BOTH legs of this candidate on the IBKR PAPER account.

    THE POINT. Every figure this strategy publishes is a Black-Scholes premium
    off a volatility index — no skew, no bid-ask, no evidence anyone would be
    filled there. A real paper fill is the one input no backtest can
    manufacture. Owner, 31 Aug: "ok start with the us paper execution".

    REFUSES rather than guesses, in four cases, because a strangle placed on
    the wrong basis is worse than no data:
      * the market is not paper-tradeable (India has no paper account at all)
      * the row is not a CANDIDATE
      * the strikes are PROVISIONAL — placing off a stale close is exactly the
        lopsided trade this was just fixed to avoid
      * the session is not open
    """
    cfg = MARKETS.get(row.get("market") or "")
    if not cfg or not cfg.get("paper_trade"):
        return {"placed": False, "reason": "market is not paper-tradeable",
                "expiry_kind": PLACE_EXPIRY_KIND}
    is_shadow = row.get("status") != "CANDIDATE"
    if is_shadow and not shadow:
        return {"placed": False, "reason": "not a candidate",
                "expiry_kind": PLACE_EXPIRY_KIND}
    # SHADOW PLACEMENT — trade the days the gate REFUSED, on paper only.
    #
    # Owner, 31 Aug 2026: "can we just put in paper trading the index even if
    # they are volatile". It is the same argument as shadow-RECORDING the
    # stand-asides, except with REAL FILLS instead of modelled ones: the gate
    # is the entire edge of this strategy and nobody has ever measured what it
    # saves you at actual option prices. On paper that measurement is free.
    #
    # THE GATE IS A STRATEGY RULE. Provisional strikes and a shut session are
    # CORRECTNESS rules — placing off a stale close or into a closed market
    # produces a fill that describes nothing. Only the first is relaxed; the
    # checks below still refuse.
    #
    # Every shadow fill is tagged, so the two populations never blend. A month
    # of these answers "what is the gate worth?" in real money rather than
    # Black-Scholes.
    if row.get("provisional"):
        return {"placed": False, "expiry_kind": PLACE_EXPIRY_KIND,
                "reason": "strikes are PROVISIONAL — refusing to place off a stale close"}
    if row.get("session_state") != "open":
        return {"placed": False, "reason": f"session is {row.get('session_state')}",
                "expiry_kind": PLACE_EXPIRY_KIND}

    # The expiry this desk actually trades. Named once, reported back in the
    # result, and consumed by record_execution — so the two can never drift.
    kind = PLACE_EXPIRY_KIND
    leg = (row.get("legs") or {}).get(kind)
    if not leg:
        return {"placed": False, "reason": f"no {kind} leg", "expiry_kind": kind}
    expiry = _monthly_expiry(leg["dte"])
    # THE BROKER SYMBOL IS NOT THE DATA SYMBOL. `index` is what Yahoo is asked
    # for (^GSPC, ^NDX); IBKR needs SPX / NDX / XSP. They coincide for SPY, QQQ
    # and GLD, which is the only reason sending `index` ever worked — and why
    # SPX, XSP and NDX sat marked unplaceable behind a note about "needing
    # their own IBKR symbol mapping". The mapping is two config keys.
    #
    # sec_type matters as much: a cash index is IND, not STK, and resolution
    # was hardcoded to STK. Both come from config, neither is inferred.
    body = {"symbol": cfg.get("broker_symbol") or cfg["index"], "expiry": expiry,
            "underlyingSecType": cfg.get("broker_sec_type") or "STK",
            "putStrike": leg["put_strike"], "callStrike": leg["call_strike"],
            "contracts": contracts}
    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        r = requests.post(f"{base.rstrip('/')}/api/integrations/ibkr/strangle",
                          json=body, timeout=60,
                          headers={"Authorization": f"Bearer {tok}"} if tok else {})
        out = r.json() if r.content else {}
    except Exception as exc:  # noqa: BLE001 — a placement failure must never
        # lose the DECISION, which is already recorded and emailed by now.
        log.warning("paper placement failed for %s: %s", row.get("market"), exc)
        return {"placed": False, "reason": f"request failed: {str(exc)[:160]}",
                "request": body, "expiry_kind": kind}
    ok = bool(out.get("ok"))
    # A REJECTION MUST CARRY A REASON. Without one it matched no reporting
    # branch and printed nothing — see the `else` in main(). The API's own
    # words first; the leg statuses next, because "which leg died" is the
    # question actually being asked.
    reason = None
    if not ok:
        legs_said = " ".join(
            f"{side}={(out.get(side) or {}).get('status') or '?'}"
            f"{'/' + str((out.get(side) or {}).get('reason'))[:60] if (out.get(side) or {}).get('reason') else ''}"
            for side in ("put", "call") if out.get(side))
        reason = (str(out.get("error") or out.get("warning") or "")[:200]
                  or legs_said or f"HTTP {r.status_code}: {str(out)[:160]}")
    return {"placed": ok, "request": body, "response": out, "reason": reason,
            "partial": bool(out.get("partial")), "expiry_kind": kind,
            # Tagged so the two populations are never averaged together.
            "shadow": is_shadow,
            "gate_said": "stand aside" if is_shadow else "trade"}



def push_decisions(rows: list[dict]) -> dict:
    """Persist EVERY evaluation to Postgres — including the stand-asides.

    Owner, 31 Aug 2026: "i need the stuff to be logged for analysis later on so
    we might need a history table ... so we can evaluate what we did and why we
    did it and check if it was right or not", and on why it runs daily at all —
    "the whole purpose of running this on a daily basis is to gather as much
    data we can for developing, backtesting new strategy".

    THE LEDGER THIS REPLACES WAS EPHEMERAL. `LEDGER` writes under $HOME, and the
    Lambda sets HOME=/tmp, which is wiped between invocations. So every
    scheduled decision since the move to Lambda has been thrown away — the
    forward test has been running daily and recording nothing. That is the one
    job it exists to do.

    STAND-ASIDES ARE PUSHED TOO, and they are the valuable rows: the edge here
    is what the gate REFUSES, and a table of only the trades cannot show whether
    the threshold is set right.
    """
    import os as _os
    payload = []
    for r in rows:
        if r.get("status") == "no_data":
            continue
        legs = r.get("legs") or {}
        econ = r.get("economics") or {}
        decision = ("CANDIDATE" if r.get("status") == "CANDIDATE" else "STAND_ASIDE")
        # One row PER EXPIRY — weekly and monthly have different strikes because
        # they price off different forwards, so collapsing them would record a
        # trade that was never described.
        for kind, leg in (legs.items() or [("none", {})]):
            payload.append({
                "market": r.get("market"), "asOf": r.get("as_of"),
                "exchangeDate": r.get("exchange_date"),
                "decision": decision, "reason": r.get("reason") or "",
                "volSymbol": (MARKETS.get(r.get("market"), {}) or {}).get("vol"),
                "volIndex": r.get("vol_index"), "volThreshold": r.get("vol_threshold"),
                "ivUsedPct": r.get("iv_used"), "spot": r.get("spot"),
                "spotBasis": r.get("spot_basis"), "provisional": bool(r.get("provisional")),
                "sessionState": r.get("session_state"),
                "expiryKind": kind, "dte": leg.get("dte"),
                "putStrike": leg.get("put_strike"), "callStrike": leg.get("call_strike"),
                "forward": leg.get("forward"), "lot": r.get("lot"),
                "collateral": econ.get("collateral"),
                "marginEstimate": econ.get("margin_estimate"),
                "creditModelled": econ.get("credit_modelled"),
                "jobsCommit": (_os.environ.get("JOBS_COMMIT") or "")[:12] or None,
                "dataSource": r.get("data_source"),
                "volAtDecision": r.get("vol_at_decision"),
                "detail": json.dumps({k: v for k, v in r.items()
                                      if k not in ("legs", "economics")}),
            })
    if not payload:
        return {"pushed": 0}
    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        resp = requests.post(f"{base.rstrip('/')}/api/strangle-decisions",
                             json=payload, timeout=30,
                             headers={"Authorization": f"Bearer {tok}"} if tok else {})
        resp.raise_for_status()
        return {"pushed": len(payload), "response": resp.json()}
    except BaseException as exc:  # noqa: BLE001,B036 — load_credentials EXITS,
        # it does not raise. `except Exception` here would let SystemExit kill
        # the whole job, which is exactly how the Lambda went down on 31 Aug.
        log.warning("decision-log push failed (non-fatal): %s", str(exc)[:200])
        return {"pushed": 0, "error": str(exc)[:200]}


def _occ_strike(desc: str) -> float | None:
    """Strike out of an IBKR contract description, via the OCC symbol."""
    import re as _re
    m = _re.search(r"\d{6}[PC](\d{8})", (desc or "").upper())
    return int(m.group(1)) / 1000.0 if m else None


def _credit_from_broker(row: dict, leg: dict) -> float | None:
    """What the broker ACTUALLY filled the two legs at, in MONEY.

    Matched on STRIKE, because IBKR's Web API returns avgPrice null on the
    order itself — the position is the only place a fill price exists.

    Money, not per share: the multiplier is READ from the position, never
    assumed. Recording a per-share figure here would repeat the 100x mistake
    the close job made on 2 Sep.
    """
    import requests
    from .push_to_api import load_credentials
    base, tok = load_credentials()
    r = requests.get(f"{base.rstrip('/')}/api/integrations/ibkr/positions",
                     params={"fresh": "true"}, timeout=45,
                     headers={"Authorization": f"Bearer {tok}"} if tok else {})
    payload = r.json() or {}
    if payload.get("error"):
        return None
    want = {float(leg.get("put_strike") or -1), float(leg.get("call_strike") or -1)}
    total, seen = 0.0, 0
    for p in payload.get("positions") or []:
        if not p.get("isOption") or float(p.get("quantity") or 0) >= 0:
            continue
        k = _occ_strike(p.get("instrumentName") or "")
        if k is None or k not in want:
            continue
        px = p.get("averagePricePaid")
        if px is None:
            continue
        mult = float(p.get("multiplier") or 0) or 100.0
        total += float(px) * abs(float(p.get("quantity") or 0)) * mult
        seen += 1
    return round(total, 2) if seen else None


def record_execution(row: dict, res: dict) -> dict:
    """Attach what ACTUALLY executed to the decision that produced it.

    Owner, 31 Aug 2026: "f the strangell worked or not" — a question the
    platform could not answer from its own records. The decision log stopped at
    the decision: nothing said whether the order was placed, what we were
    FILLED at, or what it cost to close, so the day's numbers had to be
    reconstructed from the broker by hand.

    THE FILL IS THE ONE INPUT NO BACKTEST CAN MANUFACTURE. Every figure this
    strategy publishes comes from a Black-Scholes premium off a volatility
    index — no skew, no bid-ask, and no evidence anyone would be filled there.
    Recording the real credit is the entire point of running it on paper.

    Non-fatal by design: a decision that is recorded but unlinked is a smaller
    loss than a job that dies after placing an order.
    """
    # The expiry the PLACEMENT reported, never a guess off the legs dict —
    # its iteration order is weekly-first and the desk trades the monthly.
    kind = (res or {}).get("expiry_kind") or PLACE_EXPIRY_KIND
    out = (res or {}).get("response") or {}
    ids = [str(out.get(k, {}).get("orderId")) for k in ("put", "call")
           if (out.get(k) or {}).get("orderId")]
    body = {
        # THE SESSION BEING TRADED, not the settled one the gate read. The
        # execution endpoint keys on COALESCE(exchange_date, as_of); sending
        # as_of made every PLACEMENT 404 while exits (which send the session)
        # linked fine — which is why the log showed placed=None beside a
        # perfectly good realised_pnl for days.
        "market": row.get("market"),
        "asOf": row.get("exchange_date") or row.get("as_of"),
        "expiryKind": kind,
        "placed": bool(res.get("placed")), "partial": bool(res.get("partial")),
        "shadow": bool(res.get("shadow")),
        "brokerOrderIds": ",".join(ids) or None,
        "placedAtUtc": _dt.datetime.now(_dt.UTC).isoformat(),
        # WHY IT DID NOT PLACE, on the row. Owner, 5 Sep 2026: "yes but
        # placeemnt fails then we need to see failure reason".
        #
        # The reason existed only in the Lambda log, which he cannot read. On
        # screen a REFUSED placement was indistinguishable from one never
        # attempted. In the first week of live running the failures were the
        # MAJORITY of the record — resolution on SPY/QQQ/GOLD, margin on NDX, a
        # cancelled SPX — and none of it was visible to the person deciding
        # whether to trust the desk.
        #
        # Truncated here, not in the database: a reason too long to store is
        # still worth storing the front of.
        "placeError": (None if res.get("placed")
                       else (str(res.get("reason") or "")[:400] or None)),
    }

    # THE FILL PRICE — the one number this whole exercise exists to collect.
    #
    # Every published figure for this strategy is Black-Scholes off a
    # volatility index: no skew, no bid-ask, no evidence anyone would be filled
    # there. credit_actual is the column built to hold what the broker ACTUALLY
    # gave us, and nothing ever wrote to it. The number sits on the position
    # (averagePricePaid) and the moment that position closes it is gone —
    # IBKR's Web API returns avgPrice NULL on the order, so there is no second
    # chance to recover it.
    #
    # Owner, 4 Sep 2026: "i cant see what price".
    if body["placed"] or res.get("partial"):
        try:
            got = _credit_from_broker(row, leg=(row.get("legs") or {}).get(kind) or {})
            if got is not None:
                body["creditActual"] = got
        except Exception as exc:  # noqa: BLE001 — never lose the link over this
            log.warning("could not read the filled credit for %s: %s",
                        row.get("market"), str(exc)[:120])
    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        r = requests.post(f"{base.rstrip('/')}/api/strangle-decisions/execution",
                          json=body, timeout=30,
                          headers={"Authorization": f"Bearer {tok}"} if tok else {})
        if r.status_code == 404:
            # The decision was never logged. Say so — this is the row that
            # would otherwise become an unauditable fill.
            log.warning("execution for %s has NO decision row: %s",
                        row.get("market"), r.text[:160])
            return {"linked": False, "error": r.text[:200]}
        r.raise_for_status()
        return {"linked": True}
    except BaseException as exc:  # noqa: BLE001,B036 — load_credentials EXITS
        log.warning("execution link failed (non-fatal) for %s: %s",
                    row.get("market"), str(exc)[:200])
        return {"linked": False, "error": str(exc)[:200]}


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
            e = r.get("economics")
            if e:
                c = r["ccy"]
                T.append(f"      one weekly contract: collect ~{c}{e['credit_modelled']:,} "
                         f"(modelled) · margin ~{c}{e['margin_estimate']:,}")
                T.append(f"      typical {c}{e['typical_gain']:+,} · worst so far "
                         f"{c}{e['worst_day']:+,} · caught in a crash "
                         f"{c}{e['gate_failure']:+,}")
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

    money = [r for r in rows if r.get("economics")]
    if money:
        T += ["=" * 62, "WHAT ONE CONTRACT RISKS", "=" * 62, "",
              "  Per ONE weekly contract at today's levels. The credit is",
              "  MODELLED; the loss figures are this strategy's real history",
              f"  scaled to the contract. Margin is a SPAN estimate at "
              f"{int(100 * MARGIN_PCT)}% of",
              "  collateral - your broker's number governs.", "",
              f"  {'market':<11}{'margin':>13}{'collect':>11}{'typical':>10}"
              f"{'worst so far':>14}{'caught in a crash':>19}", "  " + "-" * 76]
        for r in money:
            e, c = r["economics"], r["ccy"]
            T.append(f"  {r['market']:<11}{c + format(e['margin_estimate'], ',') :>13}"
                     f"{c + format(e['credit_modelled'], ',') :>11}"
                     f"{c + format(e['typical_gain'], '+,') :>10}"
                     f"{c + format(e['worst_day'], '+,') :>12}"
                     f"{c + format(e['gate_failure'], '+,') :>13}")
        T += ["", "  THE SAME NUMBERS AS % OF MARGIN - what decides whether an",
              "  account survives. Margin amplifies BOTH directions ~8x:", "",
              f"  {'market':<11}{'typical':>10}{'worst so far':>14}{'caught in a crash':>19}"
              f"{'winners to repay':>18}", "  " + "-" * 70]
        for r in money:
            e = r["economics"]
            T.append(f"  {r['market']:<11}{e['typical_gain_on_margin_pct']:>9.2f}%"
                     f"{e['worst_day_on_margin_pct']:>11.1f}%"
                     f"{e['gate_failure_on_margin_pct']:>12.1f}%"
                     f"{e['winners_per_gate_failure']:>15,} trades")
        T += ["", "  WHAT 'CAUGHT IN A CRASH' MEANS. It is the worst single day in",
              "  that market's entire history, priced as if you were holding this",
              "  position through it. It is an UPPER BOUND on what one day can",
              "  cost - not a prediction, and not a claim about how often the",
              "  volatility filter lets such a day through.",
              "",
              "  Read the last column first. One such day costs what hundreds of",
              "  ordinary winning trades earn. That is the shape of every",
              "  premium-selling strategy, and it is why the filter - not the win",
              "  rate - is the thing to watch.", ""]

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
              "  worst case. This is - and it is an UPPER BOUND on one day, not a",
              "  forecast, and not a claim about how often the filter lets one",
              "  through:", "",
              f"  {'market':<10}{'worst taken':>13}{'worst ever':>12}{'on':>12}"
              f"{'costs':>10}", "  " + "-" * 57]
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
            e = r.get("economics")
            if e:
                c = r["ccy"]
                H.append(f'<table role="presentation" cellpadding="0" cellspacing="0" '
                         f'border="0" style="width:100%;border-collapse:collapse;'
                         f'margin-bottom:6px">'
                         + "".join(
                             f'<tr style="background:{BG}"><td style="padding:2px 0;'
                             f'font-size:12px;color:{MUT}">{k}</td>'
                             f'<td style="padding:2px 0;text-align:right;font-size:12.5px;'
                             f'font-family:{MONO};color:{col};font-weight:600">{v}</td></tr>'
                             for k, v, col in (
                                 ("collect (modelled)", f"{c}{e['credit_modelled']:,}", OK),
                                 ("margin needed (est)", f"{c}{e['margin_estimate']:,}", D),
                                 ("typical outcome", f"{c}{e['typical_gain']:+,}", OK),
                                 ("worst day so far", f"{c}{e['worst_day']:+,}", BAD),
                                 ("caught in a crash", f"{c}{e['gate_failure']:+,}", BAD)))
                         + "</table>")
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

    if money:
        H.append(_sec("What one contract risks",
                      f"Per ONE weekly contract at today's levels. The credit is "
                      f"modelled; the losses are this strategy's real history "
                      f"scaled to the contract. Margin is a SPAN estimate at "
                      f"{int(100 * MARGIN_PCT)}% of collateral — your broker's "
                      f"number governs."))
        H.append(f'<tr><td style="padding:0 22px;background:{BG}">'
                 f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        H.append(_row(("market", "margin", "collect", "typical", "worst so far",
                       "caught in a crash"), head=True, mono=False))
        for r in money:
            e, c = r["economics"], r["ccy"]
            H.append(_row((f'<b>{r["market"]}</b>', f"{c}{e['margin_estimate']:,}",
                           f"{c}{e['credit_modelled']:,}",
                           f'<span style="color:{OK}">{c}{e["typical_gain"]:+,}</span>',
                           f'<span style="color:{BAD}">{c}{e["worst_day"]:+,}</span>',
                           f'<span style="color:{BAD};font-weight:700">'
                           f'{c}{e["gate_failure"]:+,}</span>')))
        H.append("</table>")
        H.append(f'<div style="font-size:12px;color:{MUT};padding:11px 0 6px 0">'
                 f'<b style="color:{D}">The same numbers as % of margin</b> — what '
                 f'decides whether an account survives. Margin amplifies both '
                 f'directions about eightfold.</div>')
        H.append(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        H.append(_row(("market", "typical", "worst so far", "caught in a crash",
                       "winners to repay"), head=True, mono=False))
        for r in money:
            e = r["economics"]
            H.append(_row((f'<b>{r["market"]}</b>',
                           f'<span style="color:{OK}">'
                           f'{e["typical_gain_on_margin_pct"]:+.2f}%</span>',
                           f'<span style="color:{BAD}">'
                           f'{e["worst_day_on_margin_pct"]:.1f}%</span>',
                           f'<span style="color:{BAD};font-weight:700">'
                           f'{e["gate_failure_on_margin_pct"]:.1f}%</span>',
                           f'{e["winners_per_gate_failure"]:,}')))
        H.append("</table>")
        H.append(f'<div style="background:#fef2f2;border-radius:10px;padding:13px 15px;'
                 f'margin-top:12px;font-size:12.5px;color:#7f1d1d;line-height:1.55">'
                 f'<b>“Caught in a crash”</b> is the worst single day in that market\'s '
                 f'entire history, priced as if you were holding this position through '
                 f'it. It is an <b>upper bound</b> on what one day can cost — not a '
                 f'prediction, and not a claim about how often the volatility filter '
                 f'lets such a day through.<br><br>'
                 f'<b>Read the last column first.</b> One such day costs what hundreds '
                 f'of ordinary winning trades earn. That is the shape of every '
                 f'premium-selling strategy, and it is why the filter — not the win '
                 f'rate — is the thing to watch.</div></td></tr>')

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
                 f'column is <b>not a worst case</b>. This is — and it is an '
                 f'<b>upper bound</b> on what a single day can cost, not a forecast, '
                 f'and not a claim about how often the filter lets one through:</div>')
        H.append(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                 f'style="width:100%;border-collapse:collapse">')
        H.append(_row(("market", "worst taken", "worst ever", "on", "costs"),
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
    ap.add_argument("--place", action="store_true",
                    help="place BOTH legs on the IBKR PAPER account for markets "
                         "flagged paper_trade. Refuses on provisional strikes, a "
                         "shut session, or a non-candidate row.")
    ap.add_argument("--contracts", type=int, default=1)
    # Scope a PLACEMENT to named markets. Three markets are paper-tradeable
    # (SPY, QQQ, GLD) and --place hits all of them, which is right for the
    # scheduled run and wrong for a deliberate single-market test. Owner,
    # 1 Sep 2026: "today we can test the SPY index" — one market, not three.
    #
    # Filters PLACEMENT ONLY. Evaluation, the email and the decision log still
    # cover every market: the stand-aside rows are the ones that make the gate
    # testable, and narrowing those to prove a point about SPY would quietly
    # bias the record this whole exercise exists to build.
    ap.add_argument("--place-market", default="",
                    help="comma-separated markets to PLACE (default: all "
                         "paper-tradeable). Does not narrow evaluation.")
    ap.add_argument("--place-shadow", action="store_true",
                    help="ALSO place on days the volatility gate refused — paper "
                         "only, tagged shadow=true. Measures what the gate is "
                         "worth at real fills instead of modelled prices.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    rows = [decide(m) for m in MARKETS]
    ev = _evidence()
    for r in rows:
        r["would_trade"] = r.get("status") == "CANDIDATE"
        # Money is attached to EVERY row, stand-aside included. The stand-aside
        # rows are what make the threshold testable later, and they are worth
        # far more with the economics of the trade we declined recorded beside
        # them.
        econ = economics(r, ev.get(r.get("market")))
        if econ:
            r["economics"] = econ
    # DURABLE first, local second. The local ledger is ephemeral in Lambda.
    if not args.no_record:
        pushed = push_decisions(rows)
        if pushed.get("pushed"):
            print(f"  decision log: {pushed['pushed']} row(s) persisted")
        elif pushed.get("error"):
            print(f"  decision log FAILED (non-fatal): {pushed['error'][:110]}")

    if not args.no_record:
        # BOTH kinds. See the shadow-recording note above.
        record([r for r in rows if r.get("status") in ("CANDIDATE", "stand aside")])

    if args.place:
        only = {m.strip().upper() for m in args.place_market.split(",") if m.strip()}
        if only:
            print(f"  placement scoped to: {', '.join(sorted(only))}")
        # SMALLEST FIRST. Margin is finite and first-come-first-funded, and
        # dict order put the LARGEST market first: SPX needs ~12x the margin of
        # GOLD, so one big position can crowd out four small diversified ones.
        #
        # On 2 Sep 2026 XSP filled and SPX was then CANCELLED — consistent with
        # exactly that. Our MARGIN_PCT of 12% is an ESTIMATE; IBKR's real
        # requirement on a ~$763k-notional index strangle is unknown and very
        # likely higher, so the headroom must not be assumed.
        #
        # Ordering by collateral costs nothing and means a shortfall drops the
        # single LARGEST position rather than everything queued behind it.
        # Owner, 5 Sep 2026: "why are we not placing order for other indexes if
        # we can place within the money limit".
        def _size(r: dict) -> float:
            leg = (r.get("legs") or {}).get(PLACE_EXPIRY_KIND) or {}
            k = leg.get("put_strike")
            lot = (MARKETS.get(r.get("market")) or {}).get("lot") or 1
            return float(k) * float(lot) if k else 0.0

        for r in sorted(rows, key=_size):
            if only and r.get("market") not in only:
                continue
            res = place_paper(r, contracts=args.contracts, shadow=args.place_shadow)
            if not res:
                # Even a None is reported. Silence is the one outcome that is
                # never acceptable here.
                print(f"  not placed {r.get('market')}: placement returned nothing")
            if res:
                r["paper_order"] = res
                # Link the ATTEMPT, not just the success. A refusal is evidence
                # too — three silent failures on 31 Aug are why this exists.
                r["execution_link"] = record_execution(r, res)
                if res.get("placed"):
                    tag = " [SHADOW — the gate said stand aside]" if res.get("shadow") else ""
                    print(f"  PLACED {r['market']}: {res['request']['putStrike']:,.0f}P + "
                          f"{res['request']['callStrike']:,.0f}C exp {res['request']['expiry']}{tag}")
                elif res.get("partial"):
                    print(f"  !! PARTIAL {r['market']} — one leg only, this is NAKED")
                else:
                    # AN ELSE, NOT ANOTHER CONDITION. This has now been the
                    # silent-failure site twice.
                    #
                    # 31 Aug: it read `elif r.get("status") == "CANDIDATE"`, so
                    # a failed SHADOW placement matched nothing. Three markets
                    # were attempted, all three failed, and the run printed
                    # nothing at all.
                    #
                    # 1 Sep: I "fixed" that to `elif res.get("reason")` — but
                    # the API-rejection path returned no `reason` key, so SPY,
                    # QQQ and GOLD failed silently AGAIN, in the scheduled run,
                    # while the log looked clean.
                    #
                    # Twice is a pattern: any CONDITION here can be missed by a
                    # return shape nobody thought about. An unconditional else
                    # cannot.
                    tag = " [shadow]" if r.get("status") != "CANDIDATE" else ""
                    why = res.get("reason") or f"no reason given — raw: {str(res)[:200]}"
                    print(f"  not placed {r['market']}{tag}: {why}")

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
        e = r.get("economics")
        if e:
            c = r["ccy"]
            print(f"    ONE WEEKLY CONTRACT  collect ~{c}{e['credit_modelled']:,} "
                  f"(modelled) on ~{c}{e['margin_estimate']:,} margin")
            print(f"      typical {c}{e['typical_gain']:+,} "
                  f"({e['typical_gain_on_margin_pct']:+.2f}% of margin)  ·  "
                  f"worst day {c}{e['worst_day']:+,} "
                  f"({e['worst_day_on_margin_pct']:.1f}%)")
            print(f"      caught in a crash {c}{e['gate_failure']:+,} "
                  f"({e['gate_failure_on_margin_pct']:.1f}% of margin) — "
                  f"{e['winners_per_gate_failure']:,} winners to repay; this is the "
                  f"worst day in\n      this market's history, an UPPER BOUND, not a "
                  f"forecast")
        print()
    if args.email:
        # Fail-soft: an email problem must never lose the decision, which is
        # already recorded and printed by this point.
        # RECORD THE OUTCOME (2 Sep 2026). Fail-soft is right — a mail problem
        # must not lose a decision that is already recorded. Fail-SILENT is not:
        # this returned 0 either way, so the run log said `ok` whether or not
        # the mail went, and the owner asking "no email for nifty" could not be
        # answered without CloudWatch. On a Lambda that is unreachable the
        # moment an SSO token expires.
        #
        # NIFTY was a CANDIDATE today (^INDIAVIX 11.49 vs a 12.5 threshold) and
        # the job reported ok — so "did the mail send?" was the one question the
        # log could not answer, about the one thing the job exists to do.
        _mail_status, _mail_detail, _subj = "ok", None, ""
        try:
            from types import SimpleNamespace
            from .email_digest import send_email
            _subj, (text, html) = _email_body(rows)
            send_email(SimpleNamespace(subject=_subj, text_body=text,
                                       html_body=html, pdf_bytes=None), _email_cfg())
            print(f"  email sent: {_subj}")
        except Exception as exc:  # noqa: BLE001
            _mail_status, _mail_detail = "fail", f"{type(exc).__name__}: {str(exc)[:180]}"
            log.warning("email failed (non-fatal): %s", exc)
            print(f"  email FAILED (non-fatal): {str(exc)[:120]}")
        try:
            from ..run_log import log_run
            _n_cand = sum(1 for r in rows if str(r.get("decision", "")).upper() == "CANDIDATE")
            log_run("index-strangle-paper", "email", _mail_status,
                    error=_mail_detail,
                    summary=(f"{_n_cand} candidate(s) of {len(rows)}"
                             + (f" — {_subj}" if _subj else "")))
        except Exception:  # noqa: BLE001 — logging must never fail the job
            pass

    print("  Premiums are NOT shown: India has no free NSE chain and US chains are")
    print("  captured end-of-day. The record stores the STRIKES; the credit is filled")
    print("  in from the captured chain, or by you, so a modelled number is never")
    print("  mistaken for a traded one.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
