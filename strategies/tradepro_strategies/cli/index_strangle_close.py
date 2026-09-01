"""Close open paper strangles — on a profit target, or at end of day.

Owner, 31 Aug 2026: "a auto close one on either profit or end of day".

WHY THIS IS THE MISSING HALF. Every figure this strategy publishes is measured
on a SAME-DAY CLOSE — sell at the open, buy back at the close. But the file's
own notes concede that "the intraday profit target ... is not modelled, and
[it is] a thing you actually do". So the published numbers describe a trade
nobody places: they assume you sit until the bell.

A profit target should IMPROVE on close-at-close, because it banks the easy
decay in the quiet middle of the session and is flat before the late-day move
that would hurt. That is a hypothesis, not a result — which is exactly why the
exits are recorded rather than assumed. A month of real closes settles it.

TWO TRIGGERS, and the second is not optional:

  PROFIT TARGET   buy back once the credit has decayed by TARGET_PCT.
  END OF DAY      close regardless, before the bell.

The end-of-day leg is load-bearing. An external review put it precisely: the
strikes sit ~2.4 standard deviations away for ONE day but only ~0.92 across a
week. Carried overnight the geometry changes completely and none of the
published evidence describes the position any more. So the time exit is a hard
rule, not a fallback — see project_book_vol_concentration_deferred.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re as _re

log = logging.getLogger("tradepro.index_strangle_close")

# 50% of the credit collected. The convention among premium sellers, and
# deliberately not tuned: this project has twice set a parameter by judgement
# and had to retract it. It is configurable, it will be MEASURED against
# close-at-close from the recorded exits, and it should not be moved before
# there is a sample to move it on.
TARGET_PCT = 0.50
# Minutes before the close at which the time exit fires regardless of P&L.
# Wide enough to actually get filled rather than racing the bell.
EOD_MINUTES_BEFORE_CLOSE = 15


def _minutes_to_close(cfg: dict, now_utc: _dt.datetime | None = None) -> float | None:
    """Minutes until this market's close, or None when it is not open."""
    from zoneinfo import ZoneInfo
    from .index_strangle_paper import _session_state
    state, _ = _session_state(cfg, now_utc)
    if state != "open":
        return None
    now_utc = now_utc or _dt.datetime.now(_dt.UTC)
    tz = ZoneInfo(cfg.get("tz", "America/New_York"))
    local = now_utc.astimezone(tz)
    ch, cm = (int(x) for x in cfg.get("close_local", "16:00").split(":"))
    close = local.replace(hour=ch, minute=cm, second=0, microsecond=0)
    return (close - local).total_seconds() / 60.0


def decide_close(position: dict, cfg: dict,
                 now_utc: _dt.datetime | None = None) -> dict:
    """Should this position be closed now, and why?

    `position` carries the credit collected and the current cost to buy back.
    Returns a reason in every case, including "hold" — a close decision with no
    stated reason cannot be graded later.
    """
    credit = float(position.get("credit") or 0)
    cost = position.get("current_cost")
    mins = _minutes_to_close(cfg, now_utc)

    if mins is None:
        return {"close": False, "reason": "market is not open"}
    if mins <= EOD_MINUTES_BEFORE_CLOSE:
        # TIME EXIT FIRST, and unconditionally. Even at a loss: carrying a
        # short strangle overnight is a different trade from the one measured.
        return {"close": True, "trigger": "end_of_day",
                "reason": f"{mins:.0f} min to the close — the strategy is same-day, "
                          f"and carrying it overnight is a trade nothing here has measured"}
    if credit > 0 and cost is not None:
        decayed = (credit - float(cost)) / credit
        if decayed >= TARGET_PCT:
            return {"close": True, "trigger": "profit_target",
                    "decayed_pct": round(100 * decayed, 1),
                    "reason": f"credit has decayed {100 * decayed:.0f}% "
                              f"(target {100 * TARGET_PCT:.0f}%) — bank it"}
        return {"close": False,
                "reason": f"decayed {100 * decayed:.0f}% of {100 * TARGET_PCT:.0f}% target, "
                          f"{mins:.0f} min to the close"}
    return {"close": False, "reason": "no live cost to mark against — holding"}


# An IBKR option contract description embeds the OCC symbol, e.g.
#   "SPY    SEP2026 759 P [SPY   260918P00759000 100]"
# The bracketed OCC form is the ONE unambiguous part: root, YYMMDD, P/C, and
# the strike in thousandths. The human prefix ("SEP2026 759 P") carries no day,
# so it cannot identify a contract on its own — a monthly and a weekly in the
# same month look identical there.
_OCC = _re.compile(r"([A-Z]{1,6})\s*(\d{6})([PC])(\d{8})")


def parse_occ(desc: str) -> dict | None:
    """Pull symbol / expiry / right / strike out of an IBKR contract string.

    Returns None when the string does not contain an OCC symbol — and the
    caller must then LEAVE THE POSITION ALONE rather than guess. Closing the
    wrong contract is worse than closing nothing.
    """
    m = _OCC.search((desc or "").upper())
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    try:
        exp = _dt.datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        return None
    return {"symbol": root, "expiry": exp.isoformat(), "right": right,
            "strike": int(strike) / 1000.0}


def _market_for(symbol: str, markets: dict) -> tuple[str, dict] | None:
    """The configured market whose index IS this ticker.

    Deliberately an exact match on the configured `index`. This job must only
    close what the strategy could itself have opened — the account may hold
    options from the wheel, or placed by hand, and a close job that sweeps
    everything short would flatten those too. That is why this uses the
    single-leg close rather than /options/flatten.
    """
    want = (symbol or "").upper()
    for name, cfg in markets.items():
        # Match the BROKER's roots, not the data symbol. `index` is what Yahoo
        # is asked for (^GSPC), while a position carries the OCC root — and for
        # index options that root is often the PM-settled weekly (SPXW, NDXP).
        #
        # 1 Sep 2026: an SPX strangle filled as SPXW and this returned None, so
        # the close job logged "not a configured strangle market, LEFT ALONE"
        # and would have carried it OVERNIGHT — the one thing the time exit
        # exists to prevent.
        #
        # Roots are DECLARED in config, never inferred by prefix: a prefix rule
        # would quietly make SPX match SPXW today and something unintended the
        # day a new market is added.
        roots = cfg.get("broker_roots") or (cfg.get("broker_symbol") or cfg.get("index"),)
        if want in {str(r).upper() for r in roots}:
            return name, cfg
    return None


def _record_exit(base, tok, market: str, expiry: str,
                 credit: float, cost: float, trigger: str | None,
                 unmarkable: bool) -> None:
    """Attach the exit to the decision that opened the position.

    Owner, 31 Aug 2026: "f the strangell worked or not". Closing a position and
    not recording what it closed AT leaves exactly the gap that made that
    question unanswerable — the entry was known, the exit was not, so nothing
    could be graded.

    `as_of` is TODAY's date. The exit belongs to the session being closed, and
    the decision row for it was written by the same day's run.
    """
    if unmarkable:
        # No mark means no honest exit price. Record the trigger and leave the
        # money NULL rather than inventing a number that would later be
        # averaged into a published figure.
        realised = cost_out = None
    else:
        cost_out = round(cost, 2)
        realised = round(credit - cost, 2)
    body = {"market": market, "asOf": _dt.date.today().isoformat(),
            "expiryKind": "monthly" if expiry else None,
            "exitCostActual": cost_out,
            "realisedPnl": realised,
            "closeTrigger": trigger,
            "closedAtUtc": _dt.datetime.now(_dt.UTC).isoformat()}
    try:
        import requests
        H = {"Authorization": f"Bearer {tok}"} if tok else {}
        r = requests.post(f"{base.rstrip('/')}/api/strangle-decisions/execution",
                          json=body, timeout=30, headers=H)
        if r.status_code == 404:
            print(f"     (no decision row for {market} today — exit NOT linked)")
        elif not r.ok:
            print(f"     (exit link failed for {market}: {r.text[:120]})")
    except Exception as exc:  # noqa: BLE001
        print(f"     (exit link failed for {market}: {str(exc)[:120]})")


def main() -> int:
    from .index_strangle_paper import MARKETS
    ap = argparse.ArgumentParser(prog="tradepro-index-strangle-close")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what WOULD close without sending an order")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    # Open positions come from the broker, the golden source for "do we hold
    # this" — never from an OMS view that can drift.
    #
    # fresh=true is NOT optional. IBKR serves positions from its own cache that
    # does not self-clear: on 31 Aug 2026 three closed puts kept reporting as
    # open, with identical P&L, for minutes after their orders filled. A close
    # job reading that cache would re-close positions it had already closed.
    base = tok = None
    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        H = {"Authorization": f"Bearer {tok}"} if tok else {}
        r = requests.get(f"{base.rstrip('/')}/api/integrations/ibkr/positions",
                         params={"fresh": "true"}, timeout=45, headers=H)
        body = r.json() or {}
        if body.get("error"):
            # FAIL LOUD. An unreadable book is not an empty book, and treating
            # it as one would silently skip every close that was due.
            print(f"  !! could not read the broker book: {body['error']}")
            return 1
        positions = body.get("positions") or []
    except BaseException as exc:  # noqa: BLE001,B036 — load_credentials EXITS
        print(f"  !! could not read the broker book: {str(exc)[:200]}")
        return 1

    shorts = [p for p in positions
              if (p.get("assetClass") or p.get("asset_class")) == "OPT"
              and float(p.get("quantity") or 0) < 0]

    print(f"index strangle close — {_dt.datetime.now(_dt.UTC):%Y-%m-%d %H:%M}Z")
    print(f"  {len(shorts)} short option position(s) at the broker")

    results_unparsed: list = []

    # GROUP THE LEGS BEFORE DECIDING ANYTHING.
    #
    # The profit target must be judged on the PAIR's combined credit, never on
    # one leg alone. A strangle whose put has decayed 50% while its call has
    # not is not a 50% winner — and buying back only the profitable leg LEGS
    # OUT of the position, leaving a naked short of the side that is losing.
    # That is strictly worse than either holding the strangle or closing it.
    #
    # This was harmless while only puts could fill (options level 3 refused
    # every call). Level 4 was granted the same evening, so from the next
    # session both legs exist and per-leg targets would start legging out.
    groups: dict[tuple, list] = {}
    for p in shorts:
        desc = p.get("instrumentName") or p.get("contractDesc") or p.get("ticker") or ""
        occ = parse_occ(desc)
        if not occ:
            print(f"  ?? {desc[:52]} — cannot parse the contract, LEFT ALONE")
            results_unparsed.append({"contract": desc, "action": "skipped",
                                     "reason": "unparseable contract"})
            continue
        hit = _market_for(occ["symbol"], MARKETS)
        if not hit:
            print(f"  -- {desc[:52]} — not a configured strangle market, LEFT ALONE")
            results_unparsed.append({"contract": desc, "action": "skipped",
                                     "reason": f"{occ['symbol']} is not a strangle market"})
            continue
        groups.setdefault((hit[0], occ["symbol"], occ["expiry"]), []).append((p, occ, hit[1]))

    results, closed, failed = list(results_unparsed), 0, 0
    for (market, symbol, expiry), legs in groups.items():
        cfg = legs[0][2]
        # Combined credit and combined cost across every leg of this position.
        # Both are per share; a leg with no live mark makes the pair unmarkable
        # rather than silently half-counted.
        credit = cost = 0.0
        unmarkable = False
        for p, _occ, _c in legs:
            c = p.get("averagePricePaid")
            m = p.get("currentPrice")
            if c is None or m is None:
                unmarkable = True
                break
            qty = abs(float(p.get("quantity") or 0))
            credit += float(c) * qty
            cost += float(m) * qty

        verdict = decide_close(
            {"credit": None if unmarkable else credit,
             "current_cost": None if unmarkable else cost}, cfg)

        shape = "+".join(sorted(o["right"] for _p, o, _c in legs))
        label = f"{market} {expiry} [{shape}]"

        if not verdict.get("close"):
            print(f"  .. {label:<28} hold — {verdict['reason']}")
            results.append({"market": market, "expiry": expiry, "legs": len(legs),
                            "credit": credit, "current_cost": cost,
                            "action": "hold", **verdict})
            continue

        if args.dry_run:
            print(f"  ->  WOULD CLOSE {label} ({len(legs)} leg) — {verdict['reason']}")
            results.append({"market": market, "expiry": expiry, "legs": len(legs),
                            "action": "would_close", **verdict})
            continue

        # Close EVERY leg of the group. A group that half-closes is reported as
        # such — the surviving leg is naked.
        group_failed = 0
        for p, occ, _c in legs:
            qty = abs(int(float(p.get("quantity") or 0)))
            try:
                import requests
                H = {"Authorization": f"Bearer {tok}"} if tok else {}
                rr = requests.post(
                    f"{base.rstrip('/')}/api/integrations/ibkr/option-leg",
                    json={"symbol": occ["symbol"], "expiry": occ["expiry"],
                          "strike": occ["strike"], "right": occ["right"],
                          # BUY to close a short. This was MISSING, so the
                          # endpoint rejected every close with "side must be
                          # BUY or SELL" and four legs were carried overnight
                          # on 1 Sep 2026 — the exact exposure the time exit
                          # exists to prevent.
                          #
                          # It was invisible because neither the dry-run nor a
                          # "hold" tick ever POSTs. The job reported healthy
                          # for six hours and failed the first time it mattered.
                          "side": "BUY",
                          "contracts": qty, "closingOnly": True,
                          # The position's OWN conid, so the close never depends
                          # on IBKR's option chain. On 1 Sep 2026 that chain
                          # failed for every symbol probed — including SPY 758P,
                          # which we were short at the time. A close that cannot
                          # resolve cannot close, and the position sits open
                          # overnight: precisely the exposure the time exit
                          # exists to prevent.
                          "conid": p.get("conid")},
                    timeout=60, headers=H)
                out = rr.json() if rr.content else {}
            except Exception as exc:  # noqa: BLE001
                out = {"ok": False, "error": str(exc)[:200]}

            row = {"market": market, "expiry": expiry,
                   "strike": occ["strike"], "right": occ["right"], "quantity": qty,
                   **verdict}
            if out.get("ok"):
                closed += 1
                row["action"] = "closed"
                row["order_id"] = out.get("orderId")
                print(f"  OK  CLOSED {market} {occ['strike']:.0f}{occ['right']} x{qty}")
            else:
                failed += 1
                group_failed += 1
                row["action"] = "FAILED"
                row["error"] = out.get("error") or out.get("reason") or out.get("status")
                print(f"  !! FAILED to close {market} {occ['strike']:.0f}{occ['right']} "
                      f"— STILL OPEN AND SHORT — {row['error']}")
            results.append(row)

        if group_failed and group_failed < len(legs):
            print(f"  !! {label} is now HALF-CLOSED — the surviving leg is NAKED")

        # Record the EXIT against the decision that opened it, so the round
        # trip is answerable from our own records rather than reconstructed
        # from the broker by hand. Only when the whole group closed: a
        # half-closed position has no exit price, and writing one would be a
        # fiction. Non-fatal — a missing link must never abort the sweep.
        if not group_failed:
            _record_exit(base, tok, market, expiry, credit, cost,
                         verdict.get("trigger"), unmarkable)

    if failed:
        print(f"\n  !! {failed} position(s) COULD NOT BE CLOSED and remain short.")
    elif closed:
        print(f"\n  {closed} position(s) closed.")

    if args.json:
        print(json.dumps({"shorts": len(shorts), "closed": closed,
                          "failed": failed, "results": results}, indent=1, default=str))

    # Non-zero when something that should have closed did not — the scheduler
    # must be able to tell "nothing to do" from "the exit did not fire".
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
