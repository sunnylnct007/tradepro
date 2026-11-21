"""Publish the intraday setups board (no orders). See
tradepro_strategies/intraday_setups.py.

    tradepro-intraday-setups            # print
    tradepro-intraday-setups --push     # publish today-setups/intraday-setups
"""
from __future__ import annotations

import argparse
import sys

import requests

from .. import intraday_setups as I

ARTIFACT_UNIVERSE = "intraday-setups"


def attach_verdicts(art: dict, base: str, token: str) -> None:
    """Each rule's forward-record verdict, from the shadow-books artifact, so a
    setup is never read as an edge. Unreadable = NAMED, not blank."""
    try:
        r = requests.get(f"{base.rstrip('/')}/api/today-setups/shadow-books/latest",
                         headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=30)
        rules = ((r.json().get("artifact") or {}).get("intraday_gate") or {}).get("rules") or {}
        sb = {k: v["verdict"] for k, v in rules.items()}
        art["verdicts"] = {"rule7": sb.get("rule7", "not published"), "rule2": sb.get("rule2", "not published"),
                           "backtest": {k: v["note"] for k, v in I.S.BACKTEST.items()}}
    except Exception as exc:  # noqa: BLE001
        art["verdicts"] = {"error": f"shadow-books could not be read: {str(exc)[:120]}",
                           "backtest": {k: v["note"] for k, v in I.S.BACKTEST.items()}}


def text(a: dict) -> str:
    out = [f"INTRADAY SETUPS — session {a['session']} as of bar {a['as_of_bar']}"
           + (" (final)" if a["session_final"] else " (in progress)")
           + f" · SPY below 200d: {a['spy_below_200d']} · QQQ {a['qqq_day_pct']:+.2f}%",
           f"  in play: {', '.join(a['in_play']) or 'none'} · rule 7 triggered: {', '.join(a['rule7_triggered']) or 'none'}"
           f" · rule 2 triggered: {', '.join(a['rule2_triggered']) or 'none'}"]
    for r in a["rows"][:12]:
        if r.get("not_scored"):
            out.append(f"  {r['symbol']:6s} NOT SCORED — {r['not_scored']}")
            continue
        out.append(f"  {r['symbol']:6s} gap {r['gap_pct']:+.2f}% day {r['day_pct']:+.2f}% vol x{r['opening_volume_x']} "
                   f"OR {r['opening_range']['position']:6s} vwap {r['vs_vwap']:5s} RS {r['rs_vs_qqq_pct']}"
                   f"  r7 {r['rule7']['triggered_at'] or '-'}  r2 {r['rule2']['triggered_at'] or '-'}")
    if a["missing"] or a["stale"]:
        out.append(f"  NOT COVERED — missing {a['missing']} stale {a['stale']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    from .push_to_api import load_credentials
    base, token = load_credentials()
    art = I.build()
    attach_verdicts(art, base, token)
    print(text(art))
    if not a.push:
        return 0
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups",
                      json={"universe": ARTIFACT_UNIVERSE, "label": "latest",
                            "uploaded_by": "tradepro-intraday-setups", "artifact": art},
                      headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=(5, 60))
    if r.status_code >= 300:
        print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    print(f"published {ARTIFACT_UNIVERSE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
