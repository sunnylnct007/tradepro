"""Audit every CLAIM on the live board — the checks the owner has been doing by eye.

Owner, 13 Sep: "i am getting frustrated where i am having to see screen and
tell u". Fair. Every defect this week was found by him looking at a screen:
the wheel's green tick over unverified prices, the watch's repair naming a
condition that was not failing, ABBV starred as a BUY while held and told to
sell, momentum rows explaining themselves with Ichimoku, "clears every gate"
beside a "failed" badge. All the same shape — a CLAIM the data does not
support — and all found by a human.

This reads what the board actually serves and applies those checks
mechanically. It asserts nothing about arithmetic (that is
audit-published-numbers.py, 46/46 clean); it asks only whether the words are
entitled to be there.

    uv run python scripts/audit-board-claims.py
"""
import re
import sys

import requests

sys.path.insert(0, ".")
from tradepro_strategies.cli.push_to_api import load_credentials  # noqa: E402

LANES = ["swing", "momentum", "wheel", "preearnings", "large_50", "high_beta",
         "post_earnings_puts"]

# Vocabulary that belongs to a specific strategy. A row from another lane
# using it is borrowing a rationale it did not run.
LANE_VOCAB = {
    "ichimoku": re.compile(r"ichimoku|above cloud|kijun|tenkan", re.I),
    "sigma_dip": re.compile(r"\bσ dip|sigma below|σ below", re.I),
}
LANE_ALLOWED = {
    "ichimoku": {"large_50", "high_beta", "preearnings"},   # setups + watch may
    "sigma_dip": {"swing"},
}

findings = []


def add(lane, sym, kind, detail):
    findings.append({"lane": lane, "symbol": sym, "kind": kind, "detail": detail})


def main():
    base, tok = load_credentials()
    base = base.rstrip("/")
    H = {"Authorization": f"Bearer {tok}"}
    seen_actions = {}          # symbol -> [(lane, action, why)]

    for lane in LANES:
        try:
            r = requests.get(f"{base}/api/today-setups/{lane}/latest",
                             headers=H, timeout=30)
            if r.status_code != 200:
                continue
            art = r.json().get("artifact") or {}
        except Exception as exc:  # noqa: BLE001
            add(lane, "-", "unreadable", str(exc)[:80])
            continue

        rows = art.get("candidates_v2") or art.get("candidates") or []
        for row in rows:
            sym = str(row.get("symbol") or "?")
            why = str(row.get("why") or "")
            tier = str(row.get("tier") or "")
            elig = bool(row.get("eligible"))
            act = str(row.get("action") or "")

            # 1. BORROWED RATIONALE — the row explains itself with another
            #    strategy's vocabulary.
            for name, pat in LANE_VOCAB.items():
                if pat.search(why) and lane not in LANE_ALLOWED[name]:
                    add(lane, sym, "borrowed-rationale",
                        f"why uses {name} language: {why[:70]}")

            # 2. ENDORSEMENT OVER A FAILED STRATEGY — a cheerful why beside a
            #    tier that says the strategy did not pass its gates.
            if tier == "failed" and elig and not re.search(
                    r"fail|not fund|unproven|backtest", why, re.I):
                add(lane, sym, "endorsement-over-failed",
                    f"tier=failed but why reads as a pass: {why[:70]}")

            # 3. EMPTY WHY on an actionable row.
            if elig and len(why.strip()) < 12:
                add(lane, sym, "no-reason", f"action={act} why={why!r}")

            # 4. AN ACTIONABLE ROW WITH NO NUMBERS in its reason.
            if elig and act in ("buy", "sell put") and not re.search(r"\d", why):
                add(lane, sym, "reason-without-numbers", why[:70])

            if act:
                seen_actions.setdefault(sym, []).append((lane, act, why[:60]))

    # 5. CROSS-LANE CONTRADICTION — the ABBV case, found mechanically.
    for sym, entries in seen_actions.items():
        acts = {a for _, a, _ in entries}
        buyish = {a for a in acts if a in ("buy", "sell put", "consider")}
        sellish = {a for a in acts if a in ("sell", "exit", "block", "close")}
        if buyish and sellish:
            add("cross-lane", sym, "contradiction",
                " | ".join(f"{ln}:{a}" for ln, a, _ in entries))

    print(f"lanes read: {len(LANES)}   ·   findings: {len(findings)}\n")
    if not findings:
        print("No unsupported claims on the board.")
        return 0
    by_kind = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    for kind, items in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print(f"{kind}  ({len(items)})")
        for f in items[:6]:
            print(f"    {f['lane']:14} {f['symbol']:6} {f['detail'][:95]}")
        if len(items) > 6:
            print(f"    … and {len(items)-6} more")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
