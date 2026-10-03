"""Signal replay — would the published signals have made money?

Owner, 3 Oct 2026: "at least we know the signals that strategy generated. we
should be able to validate if we would have made money or not."

This answers that question from three things that already exist, joined:

    the SIGNAL ARCHIVE   what each board published, frozen daily since 28 Sep
    the GOLDEN BAR STORE what prices actually did afterwards
    the strategy's OWN   exit rules — imported, never re-implemented, so the
    signal module        replay can never drift from what the engine trades

It deliberately does NOT read the broker. The broker book has been corrupted
twice (phantom shorts, the ESNT runaway); this measures the STRATEGY, and is
re-derivable by anyone from the archive + bars alone. That re-derivability is
the point — a screen you can check beats a screen you must trust.

HONESTY RULES, enforced here rather than hoped for:
  * FIRST APPEARANCE ONLY. Boards re-publish a standing signal daily; counting
    repeats would triple-weight whatever the rule kept liking.
  * A signal that cannot be measured (no bar at its signal date) is COUNTED
    AND NAMED in `unmeasured`, never silently dropped. "82 rows, 0 eligible"
    taught that lesson.
  * Open positions are OPEN — marked to the last close, reported separately,
    never blended into realised numbers. evaluated != closed.
  * Every output carries its provenance: the signal bar per trade, the archive
    window, and the last bar date the store could see.
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.request
from typing import Callable

from .signals import mean_reversion as _mr
from .signals import momentum_pullback as _mom

#: Archive name -> the signal module whose exit rules are THE rule.
STRATEGY_MODULES = {
    "swing": _mr,
    "momentum": _mom,
}

#: Sizing used for the cash columns: the configuration that passed its
#: pre-registered sizing gates (2% x 20). Echoed in the output so the numbers
#: are reproducible, overridable by the caller.
DEFAULT_CAPITAL = 150_000.0
DEFAULT_PCT = 0.02


def replay(boards: dict[str, dict], load_bars: Callable[[str], tuple[list[str], list[float]] | None],
           module, *, capital: float = DEFAULT_CAPITAL, pct: float = DEFAULT_PCT) -> dict:
    """Replay archived boards through the strategy's own exit rule.

    boards: {board_date: {"signal_bar": "YYYY-MM-DD", "symbols": [...]}}
    load_bars: symbol -> (dates, closes) from the golden store, or None.

    Pure given its inputs — no network, no clock — so the tests exercise the
    real decision path with synthetic bars.
    """
    closed: list[dict] = []
    open_: list[dict] = []
    unmeasured: list[dict] = []
    seen: set[str] = set()
    last_bar_seen = ""

    for day in sorted(boards):
        info = boards[day] or {}
        sb = info.get("signal_bar")
        for sym in info.get("symbols") or []:
            if not sym or sym in seen:
                continue
            seen.add(sym)
            if not sb:
                unmeasured.append({"symbol": sym, "board": day,
                                   "why": "board carries no signal_bar"})
                continue
            bars = load_bars(sym)
            if not bars:
                unmeasured.append({"symbol": sym, "board": day, "signal_bar": sb,
                                   "why": "no bars in the golden store"})
                continue
            dates, closes = bars
            if dates:
                last_bar_seen = max(last_bar_seen, dates[-1])
            if sb not in dates:
                unmeasured.append({"symbol": sym, "board": day, "signal_bar": sb,
                                   "why": f"signal bar {sb} not in the store "
                                          f"(store ends {dates[-1] if dates else 'empty'})"})
                continue

            i0 = dates.index(sb)
            fill = closes[i0]
            if not fill or fill <= 0:
                unmeasured.append({"symbol": sym, "board": day, "signal_bar": sb,
                                   "why": f"unusable fill price {fill!r}"})
                continue
            qty = int((capital * pct) // fill) or 1

            out = None
            for j in range(i0 + 1, len(closes)):
                exit_, why = module.exit_decision(
                    closes, j, fill_price=fill, bars_held=j - i0)
                if exit_:
                    out = (dates[j], closes[j], why, j - i0)
                    break
            if out:
                closed.append({
                    "symbol": sym, "signal_bar": sb, "entry": round(fill, 4),
                    "exit_date": out[0], "exit": round(out[1], 4),
                    "reason": out[2], "bars_held": out[3],
                    "ret_pct": round(100 * (out[1] / fill - 1), 2),
                    "pnl": round(qty * (out[1] - fill), 2), "qty": qty,
                })
            else:
                held = len(closes) - 1 - i0
                open_.append({
                    "symbol": sym, "signal_bar": sb, "entry": round(fill, 4),
                    "mark_date": dates[-1], "mark": round(closes[-1], 4),
                    "bars_held": held,
                    "ret_pct": round(100 * (closes[-1] / fill - 1), 2),
                    "pnl": round(qty * (closes[-1] - fill), 2), "qty": qty,
                })

    closed.sort(key=lambda r: r["signal_bar"])
    open_.sort(key=lambda r: -r["pnl"])
    wins = sum(1 for r in closed if r["pnl"] > 0)
    return {
        "closed": closed,
        "open": open_,
        "unmeasured": unmeasured,
        "summary": {
            "signals": len(seen),
            "closed": len(closed),
            "closed_wins": wins,
            "realised_pnl": round(sum(r["pnl"] for r in closed), 2),
            "open": len(open_),
            "open_pnl": round(sum(r["pnl"] for r in open_), 2),
            "unmeasured": len(unmeasured),
            "sizing": {"capital": capital, "pct_per_position": pct},
            "last_bar_in_store": last_bar_seen or None,
        },
        "caveats": [
            "OPEN rows are marks, not results — evaluated != closed.",
            "First appearance only: a signal re-published daily is counted once.",
            f"{len(unmeasured)} signal(s) could not be measured and are listed, "
            "not dropped." if unmeasured else
            "Every published signal was measurable against the store.",
            "Entry is the signal-bar close with no slippage or commission; "
            "treat realised numbers as a ceiling.",
        ],
    }


# ── fetch layer (network; thin by design) ──────────────────────────────────

def fetch_boards(strategy: str, api_base: str, token: str | None = None,
                 days: int | None = None) -> dict[str, dict]:
    """Pull every archived board for a strategy into replay() shape."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _get(path: str) -> dict:
        req = urllib.request.Request(f"{api_base.rstrip('/')}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    hist = _get(f"/api/today-setups/{strategy}/history")
    out: dict[str, dict] = {}
    entries = hist.get("entries") or []
    if days:
        entries = entries[:days]
    for e in entries:
        day = str(e.get("date"))[:10]
        d = _get(f"/api/today-setups/{strategy}/on/{day}")
        art = d.get("artifact") or {}
        if isinstance(art, str):
            art = json.loads(art)
        cands = art.get("candidates") or []
        out[day] = {
            "signal_bar": art.get("signal_bar"),
            "symbols": [c.get("symbol") or c.get("ticker") for c in cands],
        }
    return out


def golden_loader():
    """symbol -> (dates, closes) from the golden parquet store."""
    from .cli.build_universe import _load

    def load(sym: str):
        try:
            df = _load(sym)
        except Exception:  # noqa: BLE001 — reported as unmeasured, never raised
            return None
        if df is None or len(df) == 0:
            return None
        closes = [float(x) for x in df["close"].tolist()]
        dates = [str(x)[:10] for x in
                 (df["date"].tolist() if "date" in df.columns else df.index.tolist())]
        return dates, closes

    return load


def run(strategy: str, api_base: str, token: str | None = None, *,
        capital: float = DEFAULT_CAPITAL, pct: float = DEFAULT_PCT,
        days: int | None = None) -> dict:
    """Fetch + replay one strategy. The composition the CLI and MCP share."""
    if strategy not in STRATEGY_MODULES:
        raise ValueError(
            f"unknown strategy {strategy!r} — replayable: {sorted(STRATEGY_MODULES)}")
    boards = fetch_boards(strategy, api_base, token, days=days)
    result = replay(boards, golden_loader(), STRATEGY_MODULES[strategy],
                    capital=capital, pct=pct)
    result["strategy"] = strategy
    result["archive_days"] = len(boards)
    result["archive_window"] = (
        f"{min(boards)} → {max(boards)}" if boards else "empty")
    result["generated_at_utc"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return result
