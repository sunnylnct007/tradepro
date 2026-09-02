"""tradepro-trade-eval — is any of this actually working?

    uv run tradepro-trade-eval          # print the scorecard
    uv run tradepro-trade-eval --json

Owner, 2 Sep 2026: *"we need trade evalation"*, after *"i dont need more screens
i need trading signals"* and *"again the platform at the current stage gives me
nothinbg"*.

## Why this and not another screen

A signal is only worth acting on if it has a record. Everything on this desk
quotes a BACKTEST — Momentum's 48.8% win over 5,396 trades, Swing's six gates,
the wheel's DO NOT FUND — and none of it says what the LIVE signals did. The
owner has been asked to trust numbers measured in the past on data we have since
found four separate faults in.

So this compares, per strategy:

    what the strategy CLAIMED     from its own published evidence block
    what the live signals DID     from the OMS, at the price the signal named

and puts them side by side. A strategy whose live record diverges from its
backtest is the single most useful thing this platform can tell its owner, and
it could not say it at all before.

## What it refuses to do

**It will not compute a win rate off three trades and present it as one.** Below
a floor the answer is "too few to say", because a 67% win rate on three trades
is noise wearing a number — and this desk has already rejected six strategy
candidates for exactly that kind of claim.

**It marks every open position as OPEN, never as a win.** An unrealised gain is
not a result; the owner's own P&L card makes that distinction ("Open is soft")
and this must not undo it.

**A missing price is UNKNOWN, not zero.** The whole session has been a lesson in
what a defaulted zero does to a screen.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging

log = logging.getLogger("tradepro.trade_eval")

# Below this, a win rate is noise wearing a number.
MIN_TRADES_FOR_A_RATE = 10

# What each strategy claims, from its own published evidence. Quoted so the
# comparison is against the number the owner was actually shown.
CLAIMED = {
    "candidates_swing": {
        "label": "Swing", "win_pct": None, "trades": None,
        "note": "all six gates + a two-split test; edge THINS below the "
                "200-SMA (+0.24%/trade vs +0.93%) and turns negative in a "
                ">15% drawdown",
    },
    "candidates_momentum": {
        "label": "Momentum", "win_pct": 48.8, "trades": 5396,
        "note": "51% of trades LOSE and the median loses 0.33% — the average "
                "is carried by tail winners, so a handful of positions cannot "
                "express this edge",
    },
    "candidates_puts": {
        "label": "Puts", "win_pct": 89.5, "trades": 229,
        "note": "one market regime only (~Oct 2020 on); the 2022 check rested "
                "on NINE events",
    },
}


def _last_close(symbol: str) -> float | None:
    try:
        from .post_earnings_puts import _store
        end = _dt.datetime.now(_dt.UTC)
        start = end - _dt.timedelta(days=20)
        df = _store().get(canonical=symbol, asset_class="us_etf", resolution="1d",
                          start=start, end=end, allow_partial=True, skip_fetch=True,
                          fetched_by="trade_eval").df
        return None if df is None or df.empty else float(df["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        return None


def evaluate(base: str, token: str | None) -> dict:
    """Per-strategy live record, beside what that strategy claimed."""
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = requests.get(f"{base.rstrip('/')}/api/oms/orders", headers=headers,
                         timeout=45, params={"limit": 500})
        r.raise_for_status()
        d = r.json()
        orders = d if isinstance(d, list) else (d.get("orders") or d.get("rows") or [])
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not read the order book: {str(exc)[:140]}"}

    by_strategy: dict[str, list[dict]] = {}
    for o in orders:
        sid = str(o.get("strategyId") or o.get("strategy_id") or "")
        if not sid.startswith("candidates_"):
            continue
        sym = str(o.get("symbol") or "").split("_")[0].upper()
        ref = o.get("signalRefPrice") or o.get("signal_ref_price")
        if not sym or ref in (None, 0):
            continue
        now = _last_close(sym)
        by_strategy.setdefault(sid, []).append({
            "symbol": sym, "state": o.get("state"),
            "entry": float(ref), "now": now,
            # An OPEN position has an unrealised move, not a result. Both are
            # reported, and only CLOSED ones can score.
            "move_pct": (None if now is None else round(100 * (now / float(ref) - 1), 2)),
            "open": str(o.get("state") or "").upper() not in ("FILLED_CLOSED", "CLOSED"),
        })

    out = {"as_of": _dt.datetime.now(_dt.UTC).isoformat(), "strategies": []}
    for sid, rows in sorted(by_strategy.items()):
        claim = CLAIMED.get(sid, {"label": sid, "win_pct": None, "trades": None, "note": ""})
        moved = [r for r in rows if r["move_pct"] is not None]
        wins = [r for r in moved if r["move_pct"] > 0]
        avg = round(sum(r["move_pct"] for r in moved) / len(moved), 2) if moved else None
        enough = len(moved) >= MIN_TRADES_FOR_A_RATE
        out["strategies"].append({
            "strategy": claim["label"], "n": len(rows), "n_priced": len(moved),
            "open": sum(1 for r in rows if r["open"]),
            # A rate below the floor is NOT reported as a rate. "Too few to say"
            # is the honest answer and the one this desk has enforced on six
            # rejected strategy candidates.
            "live_win_pct": (round(100 * len(wins) / len(moved), 1) if enough else None),
            "live_win_note": (None if enough
                              else f"too few to say — {len(moved)} of "
                                   f"{MIN_TRADES_FOR_A_RATE} needed"),
            "live_avg_move_pct": avg,
            "claimed_win_pct": claim["win_pct"], "claimed_trades": claim["trades"],
            "claim_note": claim["note"],
            "positions": sorted(rows, key=lambda r: (r["move_pct"] is None, -(r["move_pct"] or 0))),
        })
    return out


def render(ev: dict) -> str:
    if ev.get("error"):
        return f"trade evaluation unavailable: {ev['error']}"
    lines = [f"TRADE EVALUATION — {ev['as_of'][:16]}Z", ""]
    if not ev["strategies"]:
        return "\n".join(lines + [
            "No signals placed yet. This scorecard fills in as they are.",
            "It compares what each strategy CLAIMED against what its live",
            "signals actually did — the one thing a backtest cannot tell you.",
        ])
    for s in ev["strategies"]:
        lines.append(f"── {s['strategy']}  ({s['n']} signal(s), {s['open']} open)")
        if s["live_win_pct"] is not None:
            lines.append(f"     live   win {s['live_win_pct']:.0f}%   avg move "
                         f"{s['live_avg_move_pct']:+.2f}%")
        else:
            lines.append(f"     live   {s['live_win_note']}"
                         + (f"   avg move {s['live_avg_move_pct']:+.2f}%"
                            if s["live_avg_move_pct"] is not None else ""))
        if s["claimed_win_pct"] is not None:
            lines.append(f"     claim  win {s['claimed_win_pct']:.1f}% over "
                         f"{s['claimed_trades']:,} backtested trades")
        if s["claim_note"]:
            lines.append(f"            {s['claim_note']}")
        for p in s["positions"]:
            mv = f"{p['move_pct']:+.2f}%" if p["move_pct"] is not None else "  —   "
            now_s = f"{p['now']:.2f}" if p["now"] is not None else "—"
            flag = "  (open)" if p["open"] else ""
            lines.append(f"       {p['symbol']:<7}{p['entry']:>9.2f} -> {now_s:>8}   {mv}{flag}")
        lines.append("")
    lines += [
        "An OPEN position shows an unrealised move, not a result.",
        f"A win rate is withheld below {MIN_TRADES_FOR_A_RATE} priced signals — a rate on",
        "a handful of trades is noise wearing a number.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-trade-eval")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    ev = evaluate(base, token)
    print(json.dumps(ev, indent=1) if args.json else render(ev))
    return 0
