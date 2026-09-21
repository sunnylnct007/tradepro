"""Current price for a HANDFUL of symbols — one definition, two lanes.

WHY IT IS HERE AND NOT IN A CLI. It was written inside swing_candidates for the
GM fix (#199); the Setups lane needed the identical thing a day later. Copying
it would have created the duplicate-definition shape that is this codebase's
most common bug — and the one that made the 20 Sep exit fix do nothing, because
the same market-hours rule existed twice and only one copy was corrected.

WHAT IT IS FOR. Re-quoting an ALREADY-COMPUTED signal at a price you could
actually pay. The signal itself stays on settled bars — that is what the
backtests measured, and chasing intraday would make it a different strategy.
What must not stay stale is the arithmetic a reader acts on: GM's board said
"needs 64%" from Friday's close while the live price made it 81%, and XOM said
"consider" on a kijun price had already broken.

WHY YAHOO AND NOT IBKR. This decorates a signal rather than feeding one. IBKR is
the golden source for bars, but the desk shares ONE market-data session, and
spending lines from it to price three names would contend with the strangle for
nothing. See project_one_session_had_no_line_budget.
"""
from __future__ import annotations

import logging

log = logging.getLogger("tradepro.live_quote")


def live_prices(symbols: list[str]) -> dict[str, float]:
    """{symbol: last price}. Missing keys mean NO QUOTE, never zero.

    Failure is not fatal and must not be silent: an empty dict leaves every
    caller's settled figures standing, and callers are expected to stamp the row
    so a missing re-quote is visible rather than mistaken for "nothing moved".
    """
    if not symbols:
        return {}
    uniq = list(dict.fromkeys(symbols))
    try:
        import yfinance as yf

        from .yahoo_session import yahoo_session
        df = yf.download(uniq, period="1d", interval="1m", progress=False,
                         session=yahoo_session(), group_by="ticker", threads=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("live re-quote unavailable (%s) — callers keep their "
                    "settled figures and must say so", str(exc)[:90])
        return {}
    out: dict[str, float] = {}
    for sym in uniq:
        # ALWAYS the nested frame. group_by="ticker" nests even for a SINGLE
        # symbol, so the obvious `df["Close"] if len(uniq) == 1` shortcut
        # raises KeyError and returns {} — which reads as "no quote" and
        # silently disables the whole re-quote on any one-candidate day. The
        # flat form is kept only as a fallback for a vendor shape change.
        col = None
        try:
            col = df[sym]["Close"].dropna()
        except Exception:  # noqa: BLE001
            try:
                col = df["Close"].dropna()
            except Exception:  # noqa: BLE001 — one bad column must not lose the rest
                continue
        try:
            if col is not None and len(col):
                out[sym] = round(float(col.iloc[-1]), 2)
        except Exception:  # noqa: BLE001
            continue
    return out
