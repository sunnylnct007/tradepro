"""The nightly daily-bar chain as ONE job, for a container that is not a laptop.

    tradepro-harvest-daily              # harvest 1d, then scope, evaluation (+mail), shadow books, sector stress
    tradepro-harvest-daily --no-email   # the same chain, evaluation not mailed (a verification run)

This is `scripts/bar-cache-harvest-daily.sh` in Python, step for step, so the
job can run as an ECS Fargate scheduled task (no 900 s ceiling; the harvest
alone takes ~1 h). Owner, 22 Nov 2026: "we are paying aws and need to leverage
their infra to have our process running".

WHAT IT RUNS, IN ORDER, AND WHY THE ORDER

    1. tradepro-bar-cache-harvest --resolution 1d    the golden store, trailing
       10-day window (latest sessions + any small gap). A FAILURE HERE STOPS
       THE CHAIN: everything below reads the bars this step writes, and a
       verdict computed on yesterday's bars and published as tonight's is the
       settled-row-shown-as-current bug in a new coat.
    2. tradepro-harvest-scope --push                 what was harvested (Data screen)
    3. tradepro-daily-evaluation --push --email      the nightly mail
    4. tradepro-shadow-books --push                  the forward records
    5. tradepro-sector-stress --push                 the events windows

Steps 2-5 each run even if an earlier one failed, and each failure is NAMED in
the exit line; the job exits non-zero if any step did. The Mac script logged
"WARNING ... see $LOG" into a file nobody opened; here the failure is the
task's exit code and its last line, which is what CloudWatch and the run log
show.

ONE WRITER. The store has one writer (11 Nov ruling), and this is it for daily
bars. It must not run on the Mac and here on the same night: the Mac plist
`com.tradepro.bar-cache-harvest-daily` is unloaded the evening the Fargate
schedule is enabled, not run alongside.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta

log = logging.getLogger("tradepro.harvest_daily")

#: Trailing calendar days the harvest refreshes — verbatim from the Mac script.
WINDOW_DAYS = 10

#: ``SYMBOLS`` is replaced with the comma-joined harvested scope — harvest-scope
#: requires ``--symbols`` (first Fargate run, 22 Nov 2026, exited 2 without it).
SYMBOLS = "<symbols>"

FOLLOW_UPS = (
    ("tradepro-harvest-scope", "tradepro_strategies.cli.harvest_scope", ["--symbols", SYMBOLS, "--push"]),
    ("tradepro-daily-evaluation", "tradepro_strategies.cli.daily_evaluation", ["--push", "--email"]),
    ("tradepro-shadow-books", "tradepro_strategies.cli.shadow_books", ["--push"]),
    ("tradepro-sector-stress", "tradepro_strategies.cli.sector_stress", ["--push"]),
)


def scope() -> list[str]:
    """What the daily harvest refreshes: the ONE definition, `universe.harvest_symbols`."""
    from ..universe import harvest_symbols
    return list(harvest_symbols())


def harvest_argv(symbols: list[str], today: date | None = None, *, api_base: str | None = None,
                 window_days: int = WINDOW_DAYS) -> list[str]:
    today = today or date.today()
    argv = ["--resolution", "1d", "--asset", "us_etf", "--allow-partial", "--verbose",
            "--from", (today - timedelta(days=window_days)).isoformat(), "--to", today.isoformat(),
            "--symbols", ",".join(symbols)]
    if api_base:
        argv += ["--api-base", api_base]
    return argv


def follow_ups(*, email: bool, symbols: list[str] = ()) -> list[tuple[str, str, list[str]]]:
    out = []
    joined = ",".join(symbols)
    for name, module, args in FOLLOW_UPS:
        args = [joined if a == SYMBOLS else a for a in args if email or a != "--email"]
        out.append((name, module, args))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-email", action="store_true",
                    help="run the evaluation without mailing it (a verification run beside the Mac)")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from .harvest_5m import _run_cli
    from .push_to_api import load_credentials
    base, _token = load_credentials()
    base = base.rstrip("/")

    syms = scope()
    if not syms:
        # Fail LOUD and stop. Harvesting nothing while reporting success is how
        # a lane goes quietly dark (the Mac script's words).
        print("harvest_daily: FATAL — harvest_symbols() returned nothing (universe file "
              "missing, or the store is gone)", file=sys.stderr)
        return 2
    log.info("daily 1d harvest: %d symbols, %d-day window, machine=%s",
             len(syms), a.window_days, os.environ.get("TRADEPRO_MACHINE") or "host")
    rc = _run_cli("tradepro-bar-cache-harvest", "tradepro_strategies.cli.bar_cache_harvest",
                  harvest_argv(syms, api_base=base, window_days=a.window_days))
    if rc:
        print(f"harvest_daily: FATAL — harvest exited {rc}; nothing below it ran "
              f"(a verdict on stale bars must not be published as tonight's)", file=sys.stderr)
        return rc
    results = {}
    for name, module, args in follow_ups(email=not a.no_email, symbols=syms):
        results[name] = _run_cli(name, module, args)
    bad = {k: v for k, v in results.items() if v}
    print(f"harvest_daily: {len(syms)} symbols harvested · "
          + (f"FAILED steps {bad}" if bad else "all steps ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
