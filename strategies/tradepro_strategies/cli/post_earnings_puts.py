"""Post-earnings put candidates — the daily screen.

WHAT THIS IS
------------
Names that reported in the last few sessions, dropped >= 8% on the print, and
sit in a market that is above its own 200-day average. For each: the strike,
the collateral, and the vol-scaled size.

TWO LAYERS, AND THAT IS THE POINT
---------------------------------
The wheel screen conflates "is this a setup?" with "what does the option cost?"
— so on 28 Aug thirty rows read "Pricing carried from the last priced screen"
and the whole board looked empty when the chain was merely dark. The owner
named the risk before this was written: *"will this again get impacted with
missing option data download"*.

    LAYER 1 — the SETUP. Report date, the drop, SPY vs its 200-SMA, the target
    strike, the size. Needs bars and an earnings date. NO option data. It
    cannot be blocked by a dark chain, a contended market-data session, or a
    closed market.

    LAYER 2 — the PRICE. Premium, open interest, spread, yield. Best-effort.
    When the chain is unavailable the row still SHOWS, carrying the strike and
    an explicit "premium unavailable" rather than vanishing.

So a dark chain costs you the yield estimate, never the candidate.

EVIDENCE + LIMITS: see `signals/post_earnings_put.py` and
POST_EARNINGS_PUT_GATES_V1.md. V2 passed all eight pre-registered gates; the
verdict on record is PAPER FORWARD TEST at small size, NOT funded. This screen
publishes eligibility, not advice.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import math
import os
import statistics as st

from ..signals.post_earnings_put import (
    DROP_PCT, DTE_TARGET, MAX_REPORT_AGE, OTM_PCT, TREND_WINDOW,
    market_ok, qualifies, size_factor, strike_for,
)

log = logging.getLogger("tradepro.post_earnings_puts")
STORE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")


_STORE_SINGLETON = None


def _store():
    """The shared BarStore, built once.

    READ THROUGH THE STORE, NOT THE FILESYSTEM (fixed 29 Aug 2026).

    This function used to `glob.glob()` the local bar-cache directory. That
    bypassed `BarStore._ensure_local`, which downloads a partition from the
    shared S3 bucket on a local miss — so the screen could only ever see what
    happened to be on THIS disk, and would silently under-read anywhere else or
    after a local prune. It also invented a Mac-only dependency that the S3
    mirror exists precisely to remove: the owner's words, "we moved the data
    harvesting to s3 ... ensure we not messing up the data harvesting and usage
    again".

    `skip_fetch=True` is the important half: read local, fall through to S3, and
    NEVER call a provider. A screen must not be able to trigger network fetches
    — that is the harvest's job, and it is how a screen ends up competing for
    the single IBKR market-data session.
    """
    global _STORE_SINGLETON
    if _STORE_SINGLETON is None:
        from pathlib import Path
        from ..bar_cache.asset_classes import UsEtfPlugin  # noqa: F401 — registers
        from ..bar_cache.store import BarStore
        _STORE_SINGLETON = BarStore(
            base_dir=Path(os.path.expanduser("~/.tradepro/bar_cache")))
    return _STORE_SINGLETON


def _bars(sym: str, sessions: int = 320):
    """Closes + dates for the last `sessions` sessions, via the store.

    320 covers the 200-session average with headroom. An earlier version read
    the last 4 MONTHLY partitions — about 84 sessions — so every symbol failed
    the 200-session length check and the screen reported "0 recent reporters"
    and a CLOSED market gate on data it actually held.
    """
    import datetime as _d
    end = _d.datetime.now(_d.UTC)
    start = end - _d.timedelta(days=int(sessions * 1.5))   # sessions -> calendar
    try:
        frame = _store().get(
            canonical=sym, asset_class="us_etf", resolution="1d",
            start=start, end=end,
            allow_partial=True,      # gaps are normal; the screen tolerates them
            skip_fetch=True,         # local + S3 only, never a provider call
            fetched_by="post_earnings_puts",
        )
    except Exception:  # noqa: BLE001 — one unreadable symbol must not kill the screen
        return None, None
    df = frame.df
    if df is None or df.empty:
        return None, None
    return ([float(x) for x in df["close"].tolist()],
            [str(x)[:10] for x in df.index])


def _annual_vol(closes, i, window=60):
    lo = max(1, i - window)
    r = [closes[k] / closes[k - 1] - 1 for k in range(lo, i + 1) if closes[k - 1] > 0]
    if len(r) < 20:
        return None
    sd = st.pstdev(r)
    return sd * math.sqrt(252) if sd > 0 else None


def _recent_reports(api_base: str) -> dict[str, str]:
    """symbol -> most recent report date, from the CENTRAL store.

    The central calendar holds one event per symbol, which is a forward
    calendar and useless for backtesting — but exactly right HERE, because the
    screen only cares about the most recent report.
    """
    out: dict[str, str] = {}
    from ..earnings import _calendar_store_events, _store_is_authoritative
    from ..universe import harvest_symbols
    today = _dt.date.today().isoformat()
    for sym in harvest_symbols(STORE):
        try:
            data = _calendar_store_events(sym, api_base) or {}
            if not _store_is_authoritative(data.get("store")):
                continue
            past = sorted(str(e["report_date"])[:10]
                          for e in (data.get("events") or [])
                          if e.get("report_date") and str(e["report_date"])[:10] <= today)
            if past:
                out[sym] = past[-1]
        except Exception:  # noqa: BLE001 — one symbol must not kill the screen
            continue
    return out


# US equity options list on $2.50 increments up to $200 and $5 above it (with
# $1 increments on many liquid names). Without a chain we cannot know which a
# name uses, so this snaps to the CONSERVATIVE common grid and the row says
# "indicative" — the point is to stop printing a price that cannot be traded.
def _snap_strike(strike: float) -> float:
    step = 2.5 if strike < 200 else 5.0
    return round(round(strike / step) * step, 2)


def _tradeable_size(strike: float, factor: float) -> dict:
    """What can ACTUALLY be placed, versus what the vol rule asked for.

    THE BUG THIS FIXES (30 Aug 2026). collateral_usd was strike x 100 x
    size_factor, and size_factor is a fraction — 0.34 for MRVL. The screen
    therefore printed "$6,663" for a position whose real minimum is ONE
    contract at 194.96 x 100 = $19,496. Nobody can sell 0.34 of a contract, so
    the number understated capital at risk by ~2.9x on a screen whose entire
    purpose is telling someone what to place.

    That understatement is the dangerous direction: it reads as a small,
    well-sized position while committing three times the collateral.

    NOT A RESTRICTION. Owner, 30 Aug 2026: "I do not want collateral
    restrictions", consistent with the standing rule that capital never decides
    eligibility (the "Notional > per-position limit" line was removed from the
    wheel email for the same reason). Nothing here filters, blocks or demotes a
    candidate — the only things that reject a name are the earnings drop and
    the SPY 200-SMA gate. This function exists solely so the displayed number is
    ARITHMETICALLY REAL: whole contracts at a listed strike.

    collateral_target_usd is kept in the payload for the record but is not
    surfaced; it is what the vol rule asked for, which is a risk note, not a
    limit.
    """
    snapped = _snap_strike(strike)
    per_contract = snapped * 100.0
    contracts = max(1, int(round(factor)))
    actual = per_contract * contracts
    return {
        "strike_indicative": snapped,
        "contracts": contracts,
        "collateral_actual_usd": round(actual, 0),
        "collateral_target_usd": round(per_contract * factor, 0),
    }


def scan(api_base: str) -> tuple[list[dict], list[dict], dict]:
    """Returns (candidates, near_misses, market)."""
    spy_c, spy_d = _bars("SPY")
    market: dict = {"ok": None, "reason": "SPY bars unavailable"}
    if spy_c and len(spy_c) > TREND_WINDOW:
        sma = sum(spy_c[-TREND_WINDOW:]) / TREND_WINDOW
        ok = market_ok(spy_c[-1], sma)
        market = {
            "ok": ok, "spy_close": round(spy_c[-1], 2), "spy_sma200": round(sma, 2),
            "pct_above": round(100 * (spy_c[-1] / sma - 1), 2), "as_of": spy_d[-1],
            "reason": ("SPY above its 200-day average" if ok else
                       "SPY BELOW its 200-day average — the regime gate is CLOSED"),
        }

    reports = _recent_reports(api_base)
    cands: list[dict] = []
    near: list[dict] = []

    for sym, rdate in reports.items():
        c, d = _bars(sym)
        if not c or len(c) < TREND_WINDOW + 5:
            continue
        try:
            i = d.index(rdate)
        except ValueError:
            continue
        j = i + 1                                   # the session AFTER the print
        if j >= len(c):
            continue                                # reaction not printed yet
        age = len(c) - 1 - j
        if age > MAX_REPORT_AGE:
            continue
        move = c[j] / c[j - 1] - 1 if c[j - 1] > 0 else None
        vol = _annual_vol(c, len(c) - 1)
        spot = c[-1]
        row = {
            "symbol": sym, "report_date": rdate, "sessions_since": age,
            "report_move_pct": round(100 * move, 2) if move is not None else None,
            "spot": round(spot, 2),
            "strike": strike_for(spot),
            "otm_pct": round(100 * OTM_PCT, 1),
            "dte_target": DTE_TARGET,
            "annual_vol_pct": round(100 * vol, 1) if vol else None,
            "size_factor": round(size_factor(vol), 2),
            "collateral_usd": round(strike_for(spot) * 100 * size_factor(vol), 0),
        }
        row.update(_tradeable_size(row["strike"], size_factor(vol)))
        if not qualifies(move):
            row["why_not"] = (f"fell {100 * move:.1f}% on the report, needs "
                              f"{100 * DROP_PCT:.0f}%") if move is not None \
                else "report-day move unavailable"
            near.append(row)
            continue
        if market.get("ok") is not True:
            row["why_not"] = market["reason"]
            near.append(row)
            continue
        cands.append(row)

    cands.sort(key=lambda r: r.get("report_move_pct") or 0)
    near.sort(key=lambda r: r.get("report_move_pct") or 0)
    return cands, near, market



# How far a LISTED strike/expiry may sit from the target before we refuse to
# price it. A plausible premium for the wrong contract is the worst output this
# screen could produce.
STRIKE_TOLERANCE = 0.03      # 3% of the target strike
DTE_TOLERANCE = 10           # days


def price_candidates(cands: list[dict], base: str, token: str | None) -> int:
    """Attach REAL option pricing to each candidate. Returns how many were priced.

    Owner, 30 Aug: *"a screen saying put and showing almost nothing"*. Fair — it
    told you to sell a put at 194.96 and never what you would be PAID for it.
    Premium, yield and delta are the numbers a human actually decides on, and
    until today they were unobtainable: IBKR served the option IV as the string
    "57.2%" and the parser discarded it on the suffix (806f806), so the chain
    came back empty and the screen was built to survive without it.

    THE BARS-ONLY DESIGN IS PRESERVED DELIBERATELY. Strike and size still come
    from bars, so a dark chain can never HIDE a setup — that was the right call
    and it stays. This only ADDS what the chain knows on top. A candidate with no
    chain data keeps every field it had; it simply says the premium is unknown
    rather than silently dropping off the board.
    """
    import requests
    H = {"Authorization": f"Bearer {token}"} if token else {}
    priced = 0
    for c in cands:
        sym, strike = c.get("symbol"), c.get("strike")
        if not sym or not strike:
            continue
        # TWO calls, deliberately. The first read returns availableExpiries and
        # whatever expiry IBKR defaults to — which is the NEAREST weekly, not the
        # 30-day contract this strategy trades. Pricing the default gave MRVL a
        # 5-day 212.5 put against a 30-day 195 target: a real premium for a
        # contract the strategy would never sell.
        import datetime as _dt
        import time as _t
        target_dte = int(c.get("dte_target") or 30)
        try:
            # maxStrikes=8, NOT 1. availableExpiries is derived from the legs the
            # chain actually RESOLVED, not from a separate listing — so asking for
            # one strike under-reports the expiry list. Measured on MRVL:
            #   maxStrikes=1 -> ['20260904','20260911','20260918']
            #   maxStrikes=2 -> [..., '20260925']
            # The 26-day expiry (4 days from a 30-day target) was invisible, so
            # the nearest candidate looked like 19 days, missed the tolerance, and
            # the row reported "no expiry near the target" — a limit created by my
            # own cheap discovery call, not by the listing.
            r0 = requests.get(f"{base.rstrip('/')}/api/ibkr/chain/{sym}",
                              params={"maxStrikes": 8, "right": "P"},
                              headers=H, timeout=180)
            j0 = r0.json() if r0.status_code == 200 else {}
            expiries = [str(x) for x in (j0.get("availableExpiries") or []) if str(x).isdigit()]
        except Exception as exc:  # noqa: BLE001 — pricing is additive, never fatal
            c["pricing_note"] = f"chain unavailable ({str(exc)[:60]})"
            continue
        if not expiries:
            c["pricing_note"] = "chain served no expiry list"
            continue
        today = _dt.date.today()
        def _dte(e):
            return (_dt.date(int(e[:4]), int(e[4:6]), int(e[6:8])) - today).days
        # Nearest expiry to target that is still in the future.
        future = [e for e in expiries if _dte(e) > 0]
        if not future:
            c["pricing_note"] = "no future expiry listed"
            continue
        chosen = min(future, key=lambda e: abs(_dte(e) - target_dte))
        try:
            # Wide strike window: a 10% OTM put sits well below spot, and the
            # default window centres on spot and never reaches it.
            # Poll for bid/ask. Per IBKR's spec the first snapshot call for a conid
            # is a PRE-FLIGHT that "will not deliver any data" — and these option
            # legs are freshly subscribed the moment we resolve them, so the first
            # read reliably returns IV and greeks (computed server-side) with
            # bid/ask still empty. Measured on MRVL 195P: strike, expiry, IV 56.2%
            # and delta -0.22 all present, bid/ask null.
            #
            # Giving up there produced "chain returned the leg but no bid/ask" on a
            # contract IBKR quotes perfectly well.
            legs = []
            for _try in range(4):
                r = requests.get(f"{base.rstrip('/')}/api/ibkr/chain/{sym}",
                                 params={"maxStrikes": 60, "right": "P", "expiry": chosen},
                                 headers=H, timeout=180)
                legs = (r.json() or {}).get("legs") or [] if r.status_code == 200 else []
                if any(l.get("bid") is not None or l.get("ask") is not None for l in legs):
                    break
                _t.sleep(2.5)
        except Exception as exc:  # noqa: BLE001
            c["pricing_note"] = f"chain unavailable ({str(exc)[:60]})"
            continue
        puts = [l for l in legs if (l.get("right") or "").upper().startswith("P")
                and l.get("strike") is not None]
        if not puts:
            c["pricing_note"] = "no put legs returned for this expiry"
            continue
        # Nearest LISTED strike to the bars-derived target — but ONLY if it is
        # actually near it.
        #
        # Caught before shipping: without a tolerance this priced MRVL's 212.5
        # put (1.8% OTM, delta -0.388) against a 194.96 target (10% OTM),
        # because the chain window never reached 195 and min() happily returned
        # the closest thing it had. A real premium for the WRONG contract is
        # worse than no premium — it is a number a human would act on.
        leg = min(puts, key=lambda l: abs(float(l["strike"]) - float(strike)))
        drift = abs(float(leg["strike"]) - float(strike)) / float(strike)
        if drift > STRIKE_TOLERANCE:
            c["pricing_note"] = (
                f"no listed strike near {strike:.2f} — nearest was "
                f"{leg['strike']} ({100 * drift:.1f}% away). Chain window does not "
                "reach the target; NOT priced rather than priced wrongly.")
            continue
        # Same for the expiry: a 5-day contract is not a 30-day trade.
        exp = str(leg.get("maturityDate") or "")
        if exp:
            import datetime as _dt
            try:
                days = (_dt.date(int(exp[:4]), int(exp[4:6]), int(exp[6:8]))
                        - _dt.date.today()).days
                if abs(days - int(c.get("dte_target") or 30)) > DTE_TOLERANCE:
                    c["pricing_note"] = (
                        f"nearest listed expiry is {days}d out, target "
                        f"{c.get('dte_target')}d — NOT priced rather than priced "
                        "on the wrong expiry.")
                    continue
                c["dte_actual"] = days
            except ValueError:
                pass
        bid, ask = leg.get("bid"), leg.get("ask")
        mid = round((bid + ask) / 2, 2) if (bid is not None and ask is not None) else None
        c["listed_strike"] = leg.get("strike")
        c["expiry"] = leg.get("maturityDate")
        c["bid"], c["ask"], c["mid"] = bid, ask, mid
        c["iv_pct"] = leg.get("impliedVolPct")
        c["delta"] = leg.get("delta")
        if mid and leg.get("strike"):
            collateral = float(leg["strike"]) * 100.0
            dte = max(1, int(c.get("dte_target") or 30))
            c["premium_usd"] = round(mid * 100, 2)
            # Return ON THE COLLATERAL a cash-secured put actually ties up, not on
            # the premium — the number that decides whether this beats cash.
            c["yield_pct"] = round(100 * (mid * 100) / collateral, 2)
            c["annual_yield_pct"] = round(100 * (mid * 100) / collateral * 365 / dte, 1)
            c["breakeven"] = round(float(leg["strike"]) - mid, 2)
            # |delta| is the market's own estimate of ending in-the-money.
            if leg.get("delta") is not None:
                c["assign_prob_pct"] = round(abs(float(leg["delta"])) * 100, 1)
            priced += 1
        else:
            c["pricing_note"] = "chain returned the leg but no bid/ask"
    return priced


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-post-earnings-puts")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--push", action="store_true",
                    help="POST the artifact to /api/ingest/today-setups")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    base = args.api_base
    if base is None:
        from .push_to_api import load_credentials
        base, _ = load_credentials()

    cands, near, market = scan(base)

    # Price them. Additive: a candidate with no chain keeps every bars-derived
    # field and simply carries a pricing_note instead of a premium.
    _tok = None
    try:
        from .push_to_api import load_credentials as _lc
        _b, _tok = _lc()
        n_priced = price_candidates(cands, base or _b, _tok)
    except Exception as exc:  # noqa: BLE001 — never lose the scan over pricing
        n_priced = 0
        log.warning("option pricing unavailable (%s) — candidates keep their "
                    "bars-derived strike and size", str(exc)[:120])

    art = {
        "kind": "post_earnings_puts",
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "market": market,
        "rule": {
            "entry": f"report-day drop <= {100 * DROP_PCT:.0f}%, within "
                     f"{MAX_REPORT_AGE} sessions, SPY above its 200-SMA",
            "strike": f"{100 * OTM_PCT:.0f}% OTM", "dte": DTE_TARGET,
        },
        "evidence": {
            "gates_file": "POST_EARNINGS_PUT_GATES_V1.md",
            "v2_trades": 229, "v2_win_pct": 89.5, "v2_mean_pct": 1.29,
            "v2_p5_pct": -4.72, "v2_worst_pct": -23.40,
            "null_mean_pct": -0.15,
            "verdict": "PAPER FORWARD TEST at small size — NOT FUNDED",
            "limits": [
                "Earnings history begins ~Oct 2020 — one regime only.",
                "W6 (2022 not a losing year) passed on NINE events.",
                "Worst single trade after filtering and sizing: -23.4%.",
            ],
        },
        "evaluated": len(cands) + len(near),
        "candidates": cands,
        "near_misses": near[:10],
        "priced": n_priced,
    }

    if args.json:
        print(json.dumps(art, indent=1))
        return 0

    m = market
    print(f"post-earnings puts — {art['as_of_utc'][:16]}Z")
    print(f"  MARKET GATE: {'OPEN' if m.get('ok') else 'CLOSED'} — {m['reason']}")
    if m.get("spy_close"):
        print(f"    SPY {m['spy_close']} vs 200-SMA {m['spy_sma200']} "
              f"({m['pct_above']:+.2f}%) as of {m['as_of']}")
    print(f"  scanned {art['evaluated']} recent reporters · {len(cands)} candidate(s)\n")

    if cands:
        print(f"  {'sym':<7}{'reported':<12}{'move':>8}{'spot':>10}{'strike':>10}"
              f"{'vol':>7}{'contracts':>10}{'collateral':>12}")
        for r in cands:
            print(f"  {r['symbol']:<7}{r['report_date']:<12}{r['report_move_pct']:>7.1f}%"
                  f"{r['spot']:>10.2f}{r['strike_indicative']:>10.2f}"
                  f"{(r['annual_vol_pct'] or 0):>6.0f}%{r['contracts']:>10}"
                  f"{r['collateral_actual_usd']:>11,.0f}")
        print("\n  Strike and size come from BARS only — no option data needed, so a")
        print("  dark chain cannot hide a setup. Premium/OI/spread are a separate")
        print("  best-effort layer and are not required to see the candidate.")
    else:
        print("  none today.")
        if near:
            print(f"\n  CLOSEST — recent reporters that did not qualify:")
            print(f"  {'sym':<7}{'reported':<12}{'move':>8}   why not")
            for r in near[:8]:
                mv = f"{r['report_move_pct']:>7.1f}%" if r['report_move_pct'] is not None else "      —"
                print(f"  {r['symbol']:<7}{r['report_date']:<12}{mv}   {r['why_not']}")

    print(f"\n  [{art['evidence']['verdict']}]")

    if args.push:
        # Fail-soft: a push problem must never lose the scan. The numbers are
        # already on screen by this point.
        try:
            import json as _json
            import requests
            from .push_to_api import load_credentials
            b, tok = load_credentials()
            if not b:
                log.warning("no API base — not pushed")
                return 0
            r = requests.post(
                f"{b.rstrip('/')}/api/ingest/today-setups",
                json={"universe": "post_earnings_puts", "label": "latest",
                      "artifact": art},
                headers={"Authorization": f"Bearer {tok}"} if tok else {},
                timeout=30)
            print(f"  push -> HTTP {r.status_code}")

            # ── THE FORWARD-TEST RECORD (S1, OPTION_EXECUTION_SCOPE.md) ──
            #
            # The push above lands in today_setups_results, which is
            # PRIMARY KEY (universe, label) with label='latest' — every run
            # REPLACES the last. Right for "what does the screen show now",
            # useless for "what did it show on each of the last 60 days", and
            # a forward test on it would have exactly one row.
            #
            # This appends each candidate to strategy_candidate_log instead,
            # keyed by (strategy, symbol, signal_date), so the evidence
            # accumulates from day one WITHOUT needing an order path. The
            # orders are the second half; the record is the half that has to
            # start now, because it cannot be reconstructed later.
            log_rows = []
            for c in (art.get("candidates") or []):
                try:
                    log_rows.append({
                        "strategy": "post_earnings_puts",
                        "symbol": c.get("symbol"),
                        "signalDate": (art.get("as_of_utc") or "")[:10],
                        "spot": c.get("spot"),
                        "strike": c.get("strike"),
                        "targetPrice": None,      # a short put has no target
                        "stopPrice": None,        # nor a stop — it is an obligation
                        "dte": c.get("dte_target"),
                        "annualVolPct": c.get("annual_vol_pct"),
                        "sizeFactor": c.get("size_factor"),
                        # `collateral` does not exist on the row — this recorded
                        # NULL for every candidate since the log shipped. The
                        # PLACEABLE number is the one worth keeping: whole
                        # contracts, not a fractional vol-scaled target.
                        "collateral": c.get("collateral_actual_usd"),
                        "collateralTargetUsd": c.get("collateral_target_usd"),
                        "contracts": c.get("contracts"),
                        "detail": _json.dumps(c),
                    })
                except Exception:  # noqa: BLE001 — one bad row must not lose the rest
                    continue
            if log_rows:
                lr = requests.post(
                    f"{b.rstrip('/')}/api/candidate-log",
                    json=log_rows,
                    headers={"Authorization": f"Bearer {tok}"} if tok else {},
                    timeout=30)
                if lr.status_code == 200:
                    print(f"  forward-test record -> {len(log_rows)} row(s) logged")
                else:
                    # Loud: a silently-unlogged candidate is a day missing from
                    # the forward test, and it cannot be recovered afterwards.
                    log.error("candidate-log push FAILED %s: %s — TODAY IS MISSING "
                              "from the forward-test record", lr.status_code, lr.text[:160])
        except Exception as exc:  # noqa: BLE001
            log.warning("push failed: %s", exc)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
