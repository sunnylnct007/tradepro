#!/usr/bin/env python3
"""Per-symbol FUNDAMENTALS for the quality-on-earnings-drop study.

Owner, 29 Aug 2026: "when i say fundamentally its the earning ratio, p/e ratio
etc published free by yahoo or even can be in ibkr". Correct, and my "no
fundamentals feed" was about what is LOADED, not what is available.

WHAT THIS STORES, and the limit on each -- stated here so nothing built on this
file can quietly overstate itself:

  * `info`      CURRENT snapshot (trailingPE, forwardPE, priceToBook, ROE,
                margins, debtToEquity). Useful for the name-context screen.
                USELESS for a backtest: stamping today's P/E on a 2023 event is
                look-ahead bias and would manufacture an edge from nothing.

  * `annual_eps`  Diluted EPS by fiscal year, ~5 years deep. THIS is what a
                point-in-time P/E is built from: price on the day over the most
                recently REPORTED EPS as of that day.

  * `quarterly_eps`  ~5 quarters. Too shallow to backtest on its own; kept
                because it dates the most recent report precisely.

THE HARD LIMIT: 5 annual points reaches back to roughly 2021, and the earnings
CALENDAR only reaches late 2020. Any study on this covers ~2022-2026 -- a single
post-COVID bull regime. A pass there is weak evidence; a FAILURE is strong,
because the conditions are as favourable as they get.

Resumable: re-running fetches only symbols not already held.
    uv run python scripts/fetch_fundamentals.py [--limit N] [--refresh]
"""
from __future__ import annotations
import argparse, json, logging, os, sys, time

OUT = os.path.expanduser("~/.tradepro/research/fundamentals.json")
ARTIFACT_NAME = "fundamentals.json"
log = logging.getLogger("fundamentals")
KEYS = ["trailingPE","forwardPE","priceToBook","returnOnEquity","debtToEquity",
        "profitMargins","trailingEps","forwardEps","enterpriseToEbitda","marketCap"]


def _eps_series(df) -> dict:
    """{period -> diluted EPS}. Basic EPS is the fallback; None when neither."""
    if df is None or getattr(df, "empty", True):
        return {}
    for row in ("Diluted EPS", "Basic EPS"):
        if row in df.index:
            out = {}
            for c in df.columns:
                try:
                    v = df.loc[row, c]
                    out[str(c)[:10]] = None if v != v else float(v)   # NaN -> None
                except Exception:
                    continue
            return out
    return {}



def save_artifact(obj) -> None:
    """Write locally AND mirror to S3.

    Owner, 29 Aug: "data harvesting is key and i keep on repeating ... we shd
    start storing in our cheap s3 so we can leverage". Everything harvested this
    week lived only on this laptop -- the one whose battery died twice -- which
    also breaks the standing PG+S3 policy. Fail-safe: a dead mirror leaves the
    local file intact and SAYS so rather than reporting success.
    """
    from tradepro_strategies.research_store import save
    save(ARTIFACT_NAME, obj)



def push_to_api(have: dict) -> None:
    """Send the snapshot to /api/fundamentals so the DESK can see it.

    Owner, 29 Aug: "this figure shd be visible on our data harvesting screen as
    well". A JSON file on one laptop is invisible to every screen, so the
    harvest store and the thing a human reads were never the same data.

    Best-effort: a dead API leaves the local + S3 copies intact and says so.
    Silent success on a failed push is the shape this project keeps paying for.
    """
    rows = []
    for sym, rec in have.items():
        info = rec.get("info") or {}
        ann = {k: v for k, v in (rec.get("annual_eps") or {}).items() if v is not None}
        if not info and not ann:
            continue
        def g(k):
            v = info.get(k)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        rows.append({
            "symbol": sym, "source": "yfinance",
            "trailingPe": g("trailingPE"), "forwardPe": g("forwardPE"),
            "priceToBook": g("priceToBook"), "returnOnEquity": g("returnOnEquity"),
            "profitMargin": g("profitMargins"), "debtToEquity": g("debtToEquity"),
            "trailingEps": g("trailingEps"), "forwardEps": g("forwardEps"),
            "marketCap": g("marketCap"),
            "annualEps": json.dumps(ann) if ann else None,
        })
    if not rows:
        log.warning("nothing to push")
        return
    try:
        import requests
        from tradepro_strategies.cli.push_to_api import load_credentials
        base, token = load_credentials()
        r = requests.post(f"{base.rstrip('/')}/api/fundamentals", json=rows,
                          headers={"Authorization": f"Bearer {token}"}, timeout=120)
        if r.status_code == 200:
            log.info("pushed %d row(s) to the desk: %s", len(rows), r.text[:120])
        else:
            log.error("push FAILED %s: %s — the desk will show STALE fundamentals",
                      r.status_code, r.text[:200])
    except Exception as exc:  # noqa: BLE001 — never lose the harvest over a push
        log.error("push FAILED (%s) — local + S3 copies are intact, desk is stale",
                  str(exc)[:140])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    import warnings; warnings.filterwarnings("ignore")
    from tradepro_strategies.universe import universe_symbols
    from tradepro_strategies.yahoo_session import yahoo_session
    import yfinance as yf

    syms = list(universe_symbols())
    if args.limit:
        syms = syms[: args.limit]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    have = {}
    if os.path.exists(OUT) and not args.refresh:
        try: have = json.load(open(OUT))
        except Exception: have = {}
    todo = [s for s in syms if s not in have]
    log.info("fundamentals: %d symbols, %d held, %d to fetch", len(syms), len(syms)-len(todo), len(todo))

    sess = yahoo_session()
    ok = thin = 0
    for n, sym in enumerate(todo, 1):
        rec = {"info": {}, "annual_eps": {}, "quarterly_eps": {}}
        try:
            t = yf.Ticker(sym, session=sess)
            info = t.info or {}
            rec["info"] = {k: info.get(k) for k in KEYS}
            rec["annual_eps"] = _eps_series(t.income_stmt)
            rec["quarterly_eps"] = _eps_series(t.quarterly_income_stmt)
            if rec["annual_eps"]: ok += 1
            else: thin += 1
        except Exception as exc:  # noqa: BLE001 — one dead symbol must not stop the sweep
            log.warning("%s: %s", sym, str(exc)[:90])
            thin += 1
        have[sym] = rec
        if n % 20 == 0:
            save_artifact(have)
            log.info("  %d/%d  with annual EPS=%d  thin=%d", n, len(todo), ok, thin)
        time.sleep(0.6)                      # be a good citizen; the session is shared

    save_artifact(have)
    withe = sum(1 for v in have.values() if v.get("annual_eps"))
    withpe = sum(1 for v in have.values() if (v.get("info") or {}).get("trailingPE"))
    log.info("DONE: %d symbols · %d with annual EPS · %d with a current P/E -> %s",
             len(have), withe, withpe, OUT)
    push_to_api(have)
    return 0 if withe else 1


if __name__ == "__main__":
    sys.exit(main())
