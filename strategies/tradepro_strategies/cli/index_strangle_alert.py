"""Intraday alert for an open index strangle — the moment, not the decision.

WHY AN ALERT AND NOT AN ALGORITHM.

The owner's response to a position going against him is to pull the untested
leg closer, converting the strangle toward a straddle. Asked whether that could
be automated he was clear: *"dont tijk that convesrion an be done automatically
by algo"* — and he is right. The trigger is not a price, it is a judgement
about conditions. What he asked for instead: *"but what we can have is live
alert that we movin into red"*.

So this fires on the MOMENT and says nothing about what to do. The decision
stays his; the watching does not have to.

WHY IT NEEDS NO BROKER CONNECTION.

There is no Zerodha integration and IBKR options data is dark on this account.
But the alert does not need position data — it needs the STRIKES, which the
paper record already holds, and the index level, which is free. Everything here
is computed from the index against strikes recorded this morning.

THE THRESHOLDS, and where each number comes from — none are invented:

  index beyond 0.6% from the open
      The p90 intraday excursion on a low-VIX BANKNIFTY day is 1.00%
      (measured, 493 sessions). Firing at 0.6% lands BEFORE the tail rather
      than during it, which is the only useful time to be told.

  either strike within 0.35% of spot
      The strikes sit at 1.5x the expected daily move. Once price closes to
      within a third of a percent of one, the position is no longer a strangle
      in any meaningful sense — that is the condition the conversion answers.

  volatility index up 1.0 point from the entry read
      The entry premise was "volatility is low". A point of VIX says the
      premise has expired, whatever the price has done.

Each fires ONCE per session. An alert that repeats every five minutes is an
alert that gets filtered into a folder and never read again.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os

log = logging.getLogger("tradepro.index_strangle_alert")

LEDGER = os.path.expanduser("~/.tradepro/research/index_strangle_paper.json")
FIRED = os.path.expanduser("~/.tradepro/research/index_strangle_alerts_fired.json")

MOVE_PCT = 0.60        # index distance from the open, in percent
STRIKE_NEAR_PCT = 0.35 # how close price may come to a strike
VOL_RISE = 1.0         # points of the volatility index above the entry read


def _fired_today() -> set:
    today = _dt.date.today().isoformat()
    if not os.path.exists(FIRED):
        return set()
    try:
        d = json.load(open(FIRED))
    except Exception:  # noqa: BLE001
        return set()
    return set(d.get(today, []))


def _mark_fired(keys: set) -> None:
    today = _dt.date.today().isoformat()
    d = {}
    if os.path.exists(FIRED):
        try:
            d = json.load(open(FIRED))
        except Exception:  # noqa: BLE001
            d = {}
    d[today] = sorted(set(d.get(today, [])) | keys)
    d = {k: v for k, v in d.items() if k >= (_dt.date.today() - _dt.timedelta(days=7)).isoformat()}
    os.makedirs(os.path.dirname(FIRED), exist_ok=True)
    json.dump(d, open(FIRED, "w"), indent=1)


def _today_positions() -> list[dict]:
    """Strangles recorded for TODAY that were actual candidates."""
    if not os.path.exists(LEDGER):
        return []
    try:
        led = json.load(open(LEDGER))
    except Exception:  # noqa: BLE001
        return []
    today = _dt.date.today().isoformat()
    return [r for r in led
            if r.get("status") == "CANDIDATE" and str(r.get("as_of", ""))[:10] == today]


def _live(sym: str) -> tuple[float | None, float | None]:
    """(last, session open) from the 5-minute lane. Free, and ~15 minutes
    behind at worst — which is fine for a threshold that exists to catch a
    move, not to time an exit."""
    from ..yahoo_session import yahoo_session
    import yfinance as yf
    try:
        d = yf.Ticker(sym, session=yahoo_session()).history(period="2d", interval="5m")
        if d is None or not len(d):
            return None, None
        today = str(d.index[-1])[:10]
        day = d[[str(x)[:10] == today for x in d.index]]
        if not len(day):
            return None, None
        return float(day["Close"].iloc[-1]), float(day["Open"].iloc[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: live read failed: %s", sym, str(exc)[:90])
        return None, None


def _vol_now(sym: str) -> float | None:
    from ..yahoo_session import yahoo_session
    import yfinance as yf
    try:
        d = yf.Ticker(sym, session=yahoo_session()).history(period="5d", interval="1d")
        return float(d["Close"].iloc[-1]) if d is not None and len(d) else None
    except Exception:  # noqa: BLE001
        return None


def check() -> list[dict]:
    """Every threshold crossed right now that has not already fired today."""
    from .index_strangle_paper import MARKETS
    already = _fired_today()
    out: list[dict] = []
    for pos in _today_positions():
        m = pos["market"]
        cfg = MARKETS.get(m)
        if not cfg:
            continue
        last, open_ = _live(cfg["index"])
        if last is None or not open_:
            continue
        moved = 100 * (last / open_ - 1)
        alerts = []

        if abs(moved) >= MOVE_PCT:
            alerts.append(("move", f"{cfg['index']} is {moved:+.2f}% from the open "
                                   f"({open_:,.2f} -> {last:,.2f}) — past the {MOVE_PCT}% "
                                   f"mark, and the p90 excursion on a low-vol day is 1.00%"))
        for side, k in (("PUT", pos.get("put_strike")), ("CALL", pos.get("call_strike"))):
            if not k:
                continue
            dist = 100 * abs(last - k) / last
            threatened = (last <= k * (1 + STRIKE_NEAR_PCT / 100) if side == "PUT"
                          else last >= k * (1 - STRIKE_NEAR_PCT / 100))
            if threatened:
                alerts.append((f"strike_{side.lower()}",
                               f"the {k:,.2f} {side} is {dist:.2f}% away — price has closed "
                               f"on the strike; this is the condition the conversion answers"))
        v = _vol_now(cfg["vol"])
        v0 = pos.get("vol_index")
        if v is not None and v0 and (v - v0) >= VOL_RISE:
            alerts.append(("vol", f"{cfg['vol']} {v:.2f} vs {v0:.2f} at entry (+{v - v0:.2f}) "
                                  f"— the low-volatility premise has expired"))

        for key, msg in alerts:
            k = f"{m}:{key}"
            if k in already:
                continue
            out.append({"market": m, "key": k, "message": msg,
                        "index": cfg["index"], "last": round(last, 2),
                        "open": round(open_, 2), "moved_pct": round(moved, 2),
                        "put_strike": pos.get("put_strike"),
                        "call_strike": pos.get("call_strike")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-index-strangle-alert")
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="report without marking as fired")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    hits = check()
    if not hits:
        print(f"index strangle alert — {_dt.datetime.now(_dt.UTC):%H:%M}Z: nothing to report")
        return 0

    print(f"index strangle alert — {_dt.datetime.now(_dt.UTC):%H:%M}Z")
    for h in hits:
        print(f"  [{h['market']}] {h['message']}")

    if args.email:
        # Sentinels for the outcome record below — set before the attempt so a
        # failure path cannot leave them undefined.
        _mail_status, _mail_detail = "ok", None
        try:
            from types import SimpleNamespace
            from .email_digest import send_email
            from .index_strangle_paper import _email_cfg
            mk = ", ".join(sorted({h["market"] for h in hits}))
            subj = f"[ALERT] index strangle — {mk}"
            body = ["INDEX STRANGLE — INTRADAY ALERT", "",
                    "This reports a MOMENT, not a decision. What to do about it is yours;",
                    "the conversion is judgement and is deliberately not automated.", ""]
            for h in hits:
                body += [f"{h['market']} ({h['index']})",
                         f"   {h['message']}",
                         f"   open {h['open']:,}  ->  now {h['last']:,}   ({h['moved_pct']:+.2f}%)",
                         f"   strikes: {h['put_strike']:,} PUT / {h['call_strike']:,} CALL", ""]
            body += ["Each threshold fires ONCE per session — you will not be pinged again",
                     "for the same condition today.", "",
                     "PAPER RECORD. Nothing is placed by TradePro."]
            text = "\n".join(body)
            html = "<pre style=\"font-family:monospace\">" + text.replace("<", "&lt;") + "</pre>"
            send_email(SimpleNamespace(subject=subj, text_body=text,
                                       html_body=html, pdf_bytes=None), _email_cfg())
            print(f"  email sent: {subj}")
        except Exception as exc:  # noqa: BLE001 — a send failure must not re-fire tomorrow
            _mail_status = "fail"
            _mail_detail = f"{type(exc).__name__}: {str(exc)[:180]}"
            log.warning("alert email failed (non-fatal): %s", exc)
            print(f"  email FAILED (non-fatal): {str(exc)[:120]}")
        # RECORD THE OUTCOME (2 Sep 2026). Fail-soft is correct — a send problem
        # must not re-fire the threshold tomorrow. Fail-SILENT is not: the run
        # log said `ok` whether or not the mail went, so "no email for nifty"
        # could only be answered from CloudWatch, which is unreachable the
        # moment an SSO token expires.
        try:
            from ..run_log import log_run
            log_run("index-strangle-alert", "email", _mail_status,
                    error=_mail_detail,
                    summary=f"{len(hits)} threshold(s) crossed")
        except Exception:  # noqa: BLE001 — logging must never fail the job
            pass

    if not args.dry_run:
        _mark_fired({h["key"] for h in hits})
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
