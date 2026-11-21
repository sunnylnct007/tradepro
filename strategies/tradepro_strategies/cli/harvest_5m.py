"""The 5-minute harvest as ONE job, for the Lambda (21 Nov 2026).

Owner: "I still do not understand why harvest is not off mac. it should be on
lambda". It can be: the 5m bars come from IBKR's Web API through the central
backend (provider `ibkr_web`), the store writes to S3, and the Mac lane's last
run did 214 symbols in 4.5 minutes against Lambda's 900s ceiling. Nothing here
needs a laptop.

What the Mac's bar-cache-harvest.sh did for `--resolution 5m`, reproduced in
Python so there is one definition and no shell to drift:

  scope    harvest_symbols() ∪ candidates on the swing/momentum/preearnings
           boards ∪ the IBKR paper book (TRADEPRO_HARVEST_SCOPE=candidates)
  window   a trailing 7 days (the harvester's "daily mode" cannot serve a
           single session of intraday bars)
  then     the intraday shadow record, the intraday setups board and the
           market movers, each --push, each guarded — a failure is NAMED and
           does not hide the others.

Nothing here places an order. Both this and the Mac lane may run for a day
or two: they write the same bars to the same store, and the later writer
wins with the same data.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

log = logging.getLogger("tradepro.harvest_5m")

WINDOW_DAYS = 7
BOARDS = ("swing", "momentum", "preearnings")


def candidate_symbols(base: str, token: str) -> set[str]:
    """Names on the candidate boards and in the paper book — the Mac lane's
    SCOPE=candidates, verbatim in intent. Unreadable boards contribute nothing
    and are logged; they never abort the harvest."""
    import requests
    H = {"Authorization": f"Bearer {token}"} if token else {}
    out: set[str] = set()
    for lane in BOARDS:
        try:
            d = requests.get(f"{base}/api/today-setups/{lane}/latest", headers=H, timeout=20).json()
            for r in ((d.get("artifact") or {}).get("candidates_v2") or []):
                s = str(r.get("symbol") or "").upper()
                if s:
                    out.add(s)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s board unreadable (%s) — not in scope this run", lane, str(exc)[:80])
    try:
        # fresh-exempt: this read only widens the HARVEST SCOPE (which names get
        # 5m bars); it informs no trading or reconciliation decision, and a
        # fresh IBKR call every 10 minutes would spend the session's request
        # budget on a list that changes once a day.
        p = requests.get(f"{base}/api/integrations/ibkr/positions", headers=H, timeout=20).json()
        for r in (p.get("positions") or []):
            if not r.get("isOption"):
                s = str(r.get("ticker") or "").upper()
                if s:
                    out.add(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("IBKR positions unreadable (%s) — held names not in scope this run", str(exc)[:80])
    return out


def scope(base: str, token: str, store_dir=None) -> list[str]:
    from ..universe import harvest_symbols
    flagged = set(harvest_symbols(store_dir))
    if not flagged:
        raise RuntimeError("harvest_symbols() returned nothing — refusing to harvest an empty scope")
    cands = candidate_symbols(base, token)
    syms = sorted(flagged | cands)
    log.info("scope: %d candidate/held ∪ %d harvest-flagged = %d symbols for 5m", len(cands), len(flagged), len(syms))
    return syms


def harvest_argv(symbols: list[str], today: date | None = None) -> list[str]:
    today = today or date.today()
    return ["--resolution", "5m", "--asset", "us_etf", "--allow-partial", "--verbose",
            "--from", str(today - timedelta(days=WINDOW_DAYS)), "--to", str(today),
            "--symbols", ",".join(symbols)]


def _run_cli(name: str, module: str, argv: list[str]) -> int:
    """Run one CLI in-process with its own argv; a crash is NAMED and returned
    as rc 1 so the remaining steps still run."""
    import importlib
    mod = importlib.import_module(module)
    old = sys.argv
    sys.argv = [name, *argv]
    try:
        rc = int(mod.main() or 0)
    except SystemExit as exc:
        rc = int(exc.code or 0) if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001
        log.error("%s FAILED: %s: %s", name, type(exc).__name__, str(exc)[:200])
        rc = 1
    finally:
        sys.argv = old
    log.info("%s rc=%s", name, rc)
    return rc


FOLLOW_UPS = (("tradepro-intraday-shadow", "tradepro_strategies.cli.intraday_shadow", ["--push"]),
              ("tradepro-intraday-setups", "tradepro_strategies.cli.intraday_setups", ["--push"]),
              ("tradepro-market-movers", "tradepro_strategies.cli.market_movers", ["--push"]))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from .push_to_api import load_credentials
    base, token = load_credentials()
    base = base.rstrip("/")
    syms = scope(base, token)
    rc = _run_cli("tradepro-bar-cache-harvest", "tradepro_strategies.cli.bar_cache_harvest", harvest_argv(syms))
    results = {"harvest": rc}
    for name, module, args in FOLLOW_UPS:
        results[name] = _run_cli(name, module, args)
    bad = {k: v for k, v in results.items() if v}
    print(f"harvest_5m: {len(syms)} symbols · " + (f"FAILED steps {bad}" if bad else "all steps ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
