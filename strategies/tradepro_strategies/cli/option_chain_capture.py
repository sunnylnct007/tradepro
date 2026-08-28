"""Paced option-chain capture from Yahoo — the stopgap while OPRA is unsubscribed.

WHY THIS EXISTS
---------------
Every OPTIONS market-data field from IBKR is dark on this account: bid/ask,
open interest, IV (field 7283 returns the literal string "N/A") and dividend
yield (7286 absent) — while every UNDERLYING field works fine. That is the
signature of a missing OPRA entitlement, not a bug, and no amount of fallback
engineering fixes an absent subscription. The wheel board consequently shows
0 eligible with 68 rows blocked on "open interest unavailable" and 63 on
"bid-ask spread unavailable".

Yahoo publishes the same fields for free — bid, ask, openInterest,
impliedVolatility — and `quant_engine/options/chains.py` already parses exactly
those. The only obstacle is that Yahoo rate-limits aggressively, and the screen
hammers it 82 times a run.

Owner, 17 Aug 2026: *"if we rate limited we do at slow pace — we have time; we
not do algo intraday trading where time is critical."* Correct, and it is the
same reasoning already applied to IBKR pacing (9255cc5): a background capture
has no deadline, so spending minutes per symbol to get real data beats getting
none.

WHAT IT DOES
------------
Walks the universe slowly, pulls each chain once, and upserts the legs into
`option_quote_daily` — the SAME store the screen's open-interest fallback
already reads (`resolve_open_interest` → source `own_capture`). So the board
picks these up without further wiring.

Provenance is preserved: rows are written with source='yfinance_chain', never
'g3_chain'. A Yahoo quote and an IBKR quote must never be indistinguishable —
that is the rule this whole codebase has been converging on.

BACKOFF
-------
Rate-limit errors are not failures to retry immediately; they are the server
saying "later". On a 429 the pace DOUBLES for the rest of the run and the
symbol is skipped rather than retried in a tight loop, which is what earned the
limit in the first place.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date

log = logging.getLogger("tradepro.option_chain_capture")

DEFAULT_PACE_S = 20.0
MAX_PACE_S = 300.0


# The capture is only MEANINGFUL after the options close. Yahoo clears
# openInterest/bid/ask for the new session during pre-market, so a run at
# 09:54 UTC returns chains with every liquidity field null — which is exactly
# what happened on 27 Aug 2026: the 22:15 slot was missed (the Mac's battery
# died overnight), launchd caught the job up on the next maintenance wake, and
# the catch-up wrote a snapshot with OI dark on 70 of 81 symbols. The screen
# then blocked 42 rows on "Open interest < 250" and reported 0 eligible, which
# reads as "the market is illiquid" rather than "we captured at the wrong hour".
#
# A missed night is honest; a dark snapshot presented as a capture is not.
CAPTURE_OPEN_ET = 16.5    # 16:30 ET — 15 min after the 16:15 options close
CAPTURE_SHUT_ET = 4.0     # 04:00 ET — before pre-market repopulates the chain

# Floor for "did this run actually collect liquidity data". Well below the
# healthy rate (24 Aug: 82/82) and well above a dark run (27 Aug: 11/81), so it
# separates the two without flagging an ordinary patchy night.
MIN_OI_COVERAGE = 0.50

# Hard wall-clock ceiling for one capture run. At 20s pace x 82 symbols the job
# takes ~27 minutes, so this is generous by an order of magnitude and only ever
# fires on a systemic stall.
#
# It fired for real on 27 Aug 2026: the run started at its 22:15 slot and was
# still fetching at 20:38 the FOLLOWING evening -- 22 hours, straight through
# the trading day, stamping capture_date with the date it started and heading
# for a collision with the next night's run. The per-call timeout (the root
# cause, fixed in yahoo_session) is the real repair; this is the backstop, so
# that whatever hangs next cannot eat a whole day unnoticed.
MAX_RUN_S = float(os.environ.get("TRADEPRO_CAPTURE_MAX_RUN_S", 3 * 3600))


def _in_capture_window(now=None) -> tuple[bool, str]:
    """Is NOW inside the post-close window where Yahoo serves real OI?"""
    from datetime import datetime as _dtm
    from zoneinfo import ZoneInfo
    et = (now or _dtm.now(ZoneInfo("America/New_York"))).astimezone(
        ZoneInfo("America/New_York"))
    hr = et.hour + et.minute / 60.0
    ok = hr >= CAPTURE_OPEN_ET or hr < CAPTURE_SHUT_ET
    return ok, f"{et:%Y-%m-%d %H:%M} ET"


def _post_rows(rows: list[dict]) -> int:
    """Batch-upsert captured legs. Best-effort: a push failure must not lose the
    rest of the run."""
    if not rows:
        return 0
    try:
        import requests
        from .push_to_api import load_credentials
        base, token = load_credentials()
        if not base:
            log.warning("no API base — captured %d rows cannot be persisted", len(rows))
            return 0
        r = requests.post(
            f"{base.rstrip('/')}/api/options/quotes-daily",
            json={"rows": rows},
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=60)
        if r.status_code != 200:
            log.warning("quote push failed HTTP %s: %s", r.status_code, r.text[:200])
            return 0
        return int((r.json() or {}).get("upserted") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("quote push error: %s", exc)
        return 0


def capture_symbol(symbol: str, *, target_dte: int, rights: str = "PC") -> tuple[list[dict], str]:
    """Fetch one chain and shape its legs for the store. Returns (rows, status)."""
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    from ..quant_engine.options.chains import fetch_chain_result

    pricer = BlackScholesPricer(
        risk_free_rate=float(os.environ.get("TRADEPRO_RISK_FREE_RATE", "0.04")))
    chain, reason = fetch_chain_result(symbol, target_dte=target_dte, pricer=pricer)
    if chain is None:
        # Propagate the REASON so the caller can back off on a rate limit
        # instead of treating it as an ordinary missing chain.
        return [], reason
    if not chain.expiry or chain.spot <= 0:
        return [], "no_spot"

    rows: list[dict] = []
    legs = []
    if "P" in rights:
        legs += [(q, "P") for q in (chain.puts or [])]
    if "C" in rights:
        legs += [(q, "C") for q in (chain.calls or [])]
    for q, right in legs:
        # Only persist a leg that carries something the screen actually needs.
        # A row of all-nulls is noise that makes coverage look better than it is.
        if not (q.bid or q.ask or q.open_interest or q.iv):
            continue
        rows.append({
            "symbol": symbol, "expiry": chain.expiry, "strike": float(q.strike),
            "right": right,
            "bid": float(q.bid) if q.bid else None,
            "ask": float(q.ask) if q.ask else None,
            "iv": float(q.iv) if q.iv else None,
            "openInterest": int(q.open_interest) if q.open_interest is not None else None,
            "spot": float(chain.spot),
            # NEVER 'g3_chain'. A Yahoo quote and an IBKR quote must not be
            # indistinguishable in the store.
            "source": "yfinance_chain",
        })
    return rows, "ok" if rows else "empty"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", help="comma list (default: the wheel universe)")
    ap.add_argument("--dte", type=int, default=35)
    ap.add_argument("--rights", default="P", help="P, C or PC (default P — the wheel sells puts)")
    ap.add_argument("--pace", type=float, default=DEFAULT_PACE_S,
                    help=f"seconds between symbols (default {DEFAULT_PACE_S})")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true",
                    help="capture even outside the post-close window (writes a "
                         "snapshot Yahoo has not populated yet — diagnostics only)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    in_window, et_now = _in_capture_window()
    if not in_window and not args.force:
        print(f"REFUSED: {et_now} is outside the post-close capture window "
              f"({CAPTURE_OPEN_ET:.0f}:30 ET \u2192 {CAPTURE_SHUT_ET:.0f}:00 ET). "
              "Yahoo has not published this session's open interest yet, so a run now "
              "would upsert a snapshot with every liquidity field null and the wheel "
              "screen would read it as illiquidity. Skipping; the next scheduled slot "
              "will capture properly. Override with --force.", flush=True)
        return 2

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        from .options_screen import DEFAULT_UNIVERSE
        syms = list(DEFAULT_UNIVERSE)
    if args.limit:
        syms = syms[: args.limit]

    pace = args.pace
    started = time.monotonic()
    stopped_early = False
    total_rows = upserted = ok = rate_limited = failed = 0
    sym_with_oi = sym_with_ba = 0
    print(f"option-chain capture — {len(syms)} symbols, ~{args.dte} DTE, rights={args.rights}, "
          f"pace {pace:.0f}s  [{date.today()}]", flush=True)

    for i, sym in enumerate(syms):
        elapsed = time.monotonic() - started
        if elapsed > MAX_RUN_S:
            stopped_early = True
            print(f"\nDEADLINE: {elapsed/3600:.1f}h elapsed (cap {MAX_RUN_S/3600:.1f}h) — "
                  f"stopping with {len(syms) - i} symbol(s) uncaptured. A run this slow is "
                  "a stall, not a slow network; the snapshot is partial and dated today.",
                  flush=True)
            break
        if i:
            time.sleep(pace)
        try:
            rows, status = capture_symbol(sym, target_dte=args.dte, rights=args.rights)
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "RateLimit" in name:
                rate_limited += 1
                pace = min(pace * 2, MAX_PACE_S)
                print(f"  ⏳ {sym:<6} RATE LIMITED — pace doubled to {pace:.0f}s, skipping",
                      flush=True)
                continue
            failed += 1
            print(f"  ✗ {sym:<6} {name}: {str(exc)[:70]}", flush=True)
            continue

        if status == "rate_limited":
            rate_limited += 1
            pace = min(pace * 2, MAX_PACE_S)
            print(f"  ⏳ {sym:<6} RATE LIMITED — pace doubled to {pace:.0f}s, skipping",
                  flush=True)
            continue
        if status != "ok":
            failed += 1
            print(f"  · {sym:<6} {status}", flush=True)
            continue
        n = _post_rows(rows)
        total_rows += len(rows)
        upserted += n
        ok += 1
        withoi = sum(1 for r in rows if r.get("openInterest"))
        withba = sum(1 for r in rows if r.get("bid") and r.get("ask"))
        sym_with_oi += 1 if withoi else 0
        sym_with_ba += 1 if withba else 0
        print(f"  ✓ {sym:<6} {len(rows):3d} legs (OI {withoi}, bid/ask {withba}) → upserted {n}",
              flush=True)

    print(f"\nDone: {ok} captured, {rate_limited} rate-limited, {failed} failed / {len(syms)}"
          + (" [STOPPED AT DEADLINE]" if stopped_early else ""))
    print(f"      wall clock: {(time.monotonic() - started) / 60:.0f} min")
    print(f"      {total_rows} legs shaped, {upserted} upserted; final pace {pace:.0f}s")
    print(f"      liquidity coverage: OI on {sym_with_oi}/{ok} captured symbols, "
          f"bid/ask on {sym_with_ba}/{ok}")

    # A run that collected no liquidity data is a FAILED run, not a quiet one.
    # Without this it exits 0, the wrapper logs rc=0, and the only visible
    # symptom is the wheel board blaming the market for our own empty capture.
    if stopped_early:
        return 1
    if ok and (sym_with_oi / ok) < MIN_OI_COVERAGE:
        print(f"\nFAILED: open interest present on only {sym_with_oi}/{ok} symbols "
              f"({sym_with_oi / ok:.0%} < {MIN_OI_COVERAGE:.0%}). This snapshot cannot "
              "support the wheel screen's liquidity gate. Treat the board's OI "
              "rejections as UNKNOWN, not as illiquidity, until a healthy capture lands.",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
