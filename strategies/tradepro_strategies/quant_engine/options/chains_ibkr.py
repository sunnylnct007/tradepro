"""IBKR option-chain provider — real model greeks, IV, OI and bid/ask.

Why this exists: the yfinance chain (chains.py) is delayed/patchy and its IV is
often missing → our Black-Scholes delta collapses to ~0 → no strike clears the
0.20–0.35 band → the wheel screen never produces an eligible candidate. IBKR
serves model greeks (tick 13) + option open-interest (tick 101) + live/delayed
bid-ask, so deltas are real and the screen can actually fire.

Returns the SAME `OptionChain` / `OptionQuote` model as chains.py, so it's a
drop-in: the screen tries IBKR first, falls back to yfinance. Every OptionQuote
carries the IBKR model IV, so the existing `select_by_abs_delta` (BS delta from
IV) stays consistent — and we also keep the IBKR delta for sanity.

NO FALSE POSITIVES: any failure (no connection, no greeks, no strikes) returns
None → the screen falls back / the risk engine blocks with a visible reason. We
never invent a chain.

Verify live (gateway up):
    TRADEPRO_IBKR_PORT=7500 uv run python -c \
      "from tradepro_strategies.quant_engine.options.chains_ibkr import fetch_chain_ibkr; \
       c=fetch_chain_ibkr('KO'); print(c and (c.spot,c.expiry,len(c.puts)))"
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import logging
import os

from .black_scholes import BlackScholesPricer
from .chains import OptionChain, OptionQuote

log = logging.getLogger("tradepro.options.chains_ibkr")

# How many strikes either side of spot to request greeks for. The 0.20–0.35
# delta put sits a few strikes OTM (below spot); a band keeps the market-data
# request small (one line per contract) while covering the wheel's strike zone.
_STRIKE_BAND_LO = 0.80   # request puts down to 80% of spot
_STRIKE_BAND_HI = 1.02   # ...up to just above spot
_MAX_STRIKES = 24
_GREEKS_WAIT_S = 9.0     # let model greeks + OI populate after reqMktData


def _pick_expiry(expirations: set[str], target_dte: int) -> tuple[str | None, int]:
    """Nearest expiry (YYYYMMDD) to target_dte with at least 1 DTE."""
    today = _dt.date.today()
    best: tuple[int, str] | None = None
    for e in expirations:
        try:
            d = (_dt.datetime.strptime(e, "%Y%m%d").date() - today).days
        except ValueError:
            continue
        if d < 1:
            continue
        score = abs(d - target_dte)
        if best is None or score < best[0]:
            best = (score, e)
    if best is None:
        return None, 0
    exp = best[1]
    dte = (_dt.datetime.strptime(exp, "%Y%m%d").date() - today).days
    return exp, dte


def fetch_chain_ibkr(
    symbol: str,
    target_dte: int = 35,
    *,
    ib=None,
    pricer: BlackScholesPricer | None = None,
    spot: float | None = None,
) -> OptionChain | None:
    """Pull a put chain for `symbol` near `target_dte` from IBKR with real model
    greeks. Pass a connected `ib` to batch on one connection (preferred). Best
    effort → None on any failure (caller falls back / blocks)."""
    try:
        import ib_insync
    except Exception as e:  # noqa: BLE001
        log.warning("ib_insync unavailable: %s", e)
        return None

    own = ib is None
    if own:
        host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
        port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
        cid = int(os.environ.get("TRADEPRO_IBKR_CHAIN_CLIENT_ID", "119"))
        ib = ib_insync.IB()
        try:
            ib.connect(host, port, clientId=cid, timeout=20)
        except Exception as e:  # noqa: BLE001
            log.warning("%s IBKR connect failed: %s", symbol, e)
            return None
    try:
        stock = ib_insync.Stock(symbol, "SMART", "USD")
        if not ib.qualifyContracts(stock):
            log.warning("%s could not qualify underlying", symbol)
            return None

        # Spot: caller-supplied (e.g. the screen's IBKR daily close) or last trade.
        if spot is None or spot <= 0:
            bars = ib.reqHistoricalData(
                stock, endDateTime="", durationStr="2 D", barSizeSetting="1 day",
                whatToShow="TRADES", useRTH=True, formatDate=1, timeout=20)
            spot = bars[-1].close if bars else None
        if not spot or spot <= 0:
            log.warning("%s no spot", symbol)
            return None

        params = ib.reqSecDefOptParams(stock.symbol, "", "STK", stock.conId)
        if not params:
            log.warning("%s no option params", symbol)
            return None
        # Prefer the SMART chain (deepest); else the one with most strikes.
        smart = [p for p in params if p.exchange == "SMART"]
        chain_params = (smart or sorted(params, key=lambda p: len(p.strikes))[-1:])[0]

        expiry, dte = _pick_expiry(set(chain_params.expirations), target_dte)
        if not expiry:
            log.warning("%s no usable expiry", symbol)
            return None

        lo, hi = spot * _STRIKE_BAND_LO, spot * _STRIKE_BAND_HI
        strikes = sorted(k for k in chain_params.strikes if lo <= k <= hi)
        # Keep the strikes nearest spot if the band is wide.
        if len(strikes) > _MAX_STRIKES:
            strikes = sorted(strikes, key=lambda k: abs(k - spot))[:_MAX_STRIKES]
            strikes.sort()
        if not strikes:
            log.warning("%s no strikes in band around %.2f", symbol, spot)
            return None

        tc = chain_params.tradingClass
        puts = [ib_insync.Option(symbol, expiry, k, "P", "SMART", tradingClass=tc)
                for k in strikes]
        ib.qualifyContracts(*puts)
        puts = [p for p in puts if p.conId]
        if not puts:
            log.warning("%s no put contracts qualified", symbol)
            return None

        # Delayed data type 3 → works without a live options subscription and
        # outside RTH; if a live sub exists IBKR still serves live. genericTick
        # 101 = option open interest; model greeks (tick 13) arrive automatically.
        with contextlib.suppress(Exception):
            ib.reqMarketDataType(3)
        tickers = [ib.reqMktData(p, "101", False, False) for p in puts]
        ib.sleep(_GREEKS_WAIT_S)

        pricer = pricer or BlackScholesPricer()
        quotes: list[OptionQuote] = []
        for p, tk in zip(puts, tickers, strict=False):
            mg = getattr(tk, "modelGreeks", None)
            iv = float(mg.impliedVol) if (mg and mg.impliedVol and mg.impliedVol > 0) else 0.0
            bid = float(tk.bid) if (tk.bid and tk.bid > 0) else 0.0
            ask = float(tk.ask) if (tk.ask and tk.ask > 0) else 0.0
            # None = OI not served (gate says unavailable) — never a fake 0.
            oi = int(tk.putOpenInterest) if getattr(tk, "putOpenInterest", None) else None
            quotes.append(OptionQuote(kind="put", strike=float(p.strike),
                                      bid=bid, ask=ask, iv=iv, open_interest=oi))
            with contextlib.suppress(Exception):
                ib.cancelMktData(p)

        usable = [q for q in quotes if q.iv > 0]
        if not usable:
            log.warning("%s IBKR returned no usable greeks (delayed/no-sub?)", symbol)
            return None

        return OptionChain(symbol=symbol, spot=float(spot), expiry=_fmt_iso(expiry),
                           dte=dte, calls=[], puts=quotes)
    except Exception as e:  # noqa: BLE001
        log.warning("%s IBKR chain fetch failed: %s", symbol, e)
        return None
    finally:
        if own:
            with contextlib.suppress(Exception):
                ib.disconnect()


def _fmt_iso(yyyymmdd: str) -> str:
    try:
        return _dt.datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat()
    except ValueError:
        return yyyymmdd


__all__ = ["fetch_chain_ibkr"]
