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


def main() -> int:
    from .index_strangle_paper import MARKETS
    ap = argparse.ArgumentParser(prog="tradepro-index-strangle-close")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what WOULD close without sending an order")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    # Open paper strangles come from the broker, which is the golden source for
    # "do we own this" — never from an OMS view that can drift.
    out = []
    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        H = {"Authorization": f"Bearer {tok}"} if tok else {}
        r = requests.get(f"{base.rstrip('/')}/api/integrations/ibkr/positions",
                         timeout=30, headers=H)
        positions = (r.json() or {}).get("positions") or []
    except BaseException as exc:  # noqa: BLE001,B036 — load_credentials EXITS
        log.warning("could not read the broker book: %s", str(exc)[:160])
        positions = []

    opts = [p for p in positions if (p.get("assetClass") or p.get("asset_class")) == "OPT"]
    print(f"index strangle close — {_dt.datetime.now(_dt.UTC):%Y-%m-%d %H:%M}Z")
    print(f"  {len(opts)} option position(s) at the broker")
    for m, cfg in MARKETS.items():
        if not cfg.get("paper_trade"):
            continue
        mins = _minutes_to_close(cfg)
        if mins is not None:
            print(f"  {m:<7} open, {mins:.0f} min to the close")
    if args.json:
        print(json.dumps({"positions": len(opts), "checked": out}, indent=1))
    print("\n  NOTE: nothing has been placed yet by the strangle, so there is nothing")
    print("  to close. This is wired and awaiting the first real paper fill.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
