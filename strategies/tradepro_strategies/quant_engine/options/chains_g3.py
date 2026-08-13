"""G3 option-chain provider — TradePro's own live IBKR chain feed
(backend/TradePro.Api/Endpoints/ChainEndpoints.cs, GET /api/ibkr/chain/{symbol}),
shipped 2 Aug 2026 per TRADEPRO_SPEC_V2.md as the shared chain feed for the whole
system: one implementation (the API-hosted IBKRClient, OAuth session cached),
three consumers — this screen, the Symbol Lab options panel, and any MCP session
via get_option_chain.

Why prefer this over chains_ibkr.py (ib_insync/TWS socket): that path needs a
local TWS/Gateway process reachable from wherever the screen runs, which is the
exact IBKR session-contention problem already on record (harvester vs UI login
grabbing the one Web-API session). G3 goes through the API's own OAuth REST
session instead — no local Gateway dependency, and it's the SAME client every
other IBKR-backed feature in the app already uses. It also returns IBKR's real
tick greeks (delta included) rather than requiring a Black-Scholes derivation,
so OptionQuote.delta is populated directly — see chains.py's delta_of().

Returns the SAME OptionChain / OptionQuote model as chains.py/chains_ibkr.py, so
it's a drop-in: the screen can try G3 first, fall back to chains_ibkr, then
yfinance. NO FALSE POSITIVES: any failure (API unreachable, IBKR disabled, chain
error) returns None — never a fabricated chain.

Verify live:
    uv run python -c \
      "from tradepro_strategies.quant_engine.options.chains_g3 import fetch_chain_g3; \
       c = fetch_chain_g3('SPY', right='P'); print(c and (c.spot, c.expiry, len(c.puts)))"
"""
from __future__ import annotations

import datetime as _dt
import logging

import requests

from .chains import OptionChain, OptionQuote

log = logging.getLogger("tradepro.options.chains_g3")


def _month_to_date(month: str) -> str | None:
    """'AUG26' -> an ISO date for the 3rd Friday of that month (typical
    monthly-expiry convention) — good enough for the `expiry`/`dte` display
    fields; the actual tradeable contract (conid) is what secdef/info
    resolved, this is only for the DTE estimate used in yield annualisation."""
    try:
        mon = _dt.datetime.strptime(month[:3], "%b").month
        year = 2000 + int(month[3:])
        # 3rd Friday: first day of month -> first Friday -> +14 days.
        d = _dt.date(year, mon, 1)
        first_friday = d + _dt.timedelta(days=(4 - d.weekday()) % 7)
        third_friday = first_friday + _dt.timedelta(days=14)
        return third_friday.isoformat()
    except (ValueError, IndexError):
        return None


def fetch_chain_g3(
    symbol: str,
    *,
    target_dte: int = 35,
    right: str = "P",
    max_strikes: int = 20,
    expiry: str | None = None,     # exact expiry (YYYY-MM-DD) — weekly selection (SPEC §1)
    api_base: str | None = None,
    api_token: str | None = None,
    timeout: float = 30.0,
) -> OptionChain | None:
    """Fetch one expiration's chain via the deployed G3 endpoint. Picks the
    listed month closest to `target_dte` calendar days out (G3's /months call
    doesn't return DTE directly, so this estimates via 3rd-Friday convention —
    close enough to pick a month; the real DTE used downstream should come
    from the chain response once IBKR-verified fields are trustworthy).
    Returns None on ANY failure — the caller falls back, never fabricates."""
    if not symbol:
        return None
    try:
        from ...cli.push_to_api import load_credentials
        base, token = (api_base, api_token) if api_base else load_credentials()
    except SystemExit:
        log.warning("%s: no API credentials configured for G3 chain fetch", symbol)
        return None
    base = base.rstrip("/")

    try:
        months_resp = requests.get(
            f"{base}/api/ibkr/chain/{symbol}/months",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=timeout,
        )
        months_resp.raise_for_status()
        months_data = months_resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("%s: G3 months fetch failed: %s", symbol, e)
        return None
    if months_data.get("error") or not months_data.get("months"):
        log.warning("%s: G3 months unavailable: %s", symbol, months_data.get("error"))
        return None

    today = _dt.date.today()
    def _dte(m: str) -> int:
        d = _month_to_date(m)
        return abs((_dt.date.fromisoformat(d) - today).days) if d else 10_000
    # Exact-expiry mode: the month containing the requested weekly, not the
    # month nearest target_dte (they differ exactly when the short tier is
    # in play — Aug21 weekly vs the Sep monthly).
    if expiry:
        _exp_d = _dt.date.fromisoformat(expiry)
        _want_month = _exp_d.strftime("%b").upper() + _exp_d.strftime("%y")
        if _want_month not in months_data["months"]:
            log.warning("%s: requested expiry %s (month %s) not in listed months %s",
                        symbol, expiry, _want_month, months_data["months"])
            return None
        chosen_month = _want_month
    else:
        chosen_month = min(months_data["months"], key=lambda m: abs(_dte(m) - target_dte))

    try:
        _params = {"month": chosen_month, "right": right, "maxStrikes": max_strikes}
        if expiry:
            _params["expiry"] = expiry.replace("-", "")
        else:
            # Let the server pick the listed expiry closest to target_dte — it
            # holds every contract per strike, so this costs no extra IBKR
            # calls and guarantees every leg describes ONE contract inside the
            # intended DTE band.
            _params["targetDte"] = target_dte
        chain_resp = requests.get(
            f"{base}/api/ibkr/chain/{symbol}",
            params=_params,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=timeout,
        )
        chain_resp.raise_for_status()
        data = chain_resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("%s: G3 chain fetch failed: %s", symbol, e)
        return None
    if data.get("error"):
        log.warning("%s: G3 chain error: %s", symbol, data["error"])
        return None
    spot = data.get("spot")
    legs = data.get("legs") or []
    if not spot or not legs:
        log.warning("%s: G3 chain returned no spot/legs", symbol)
        return None

    # TRUE expiry: prefer the legs' own maturityDate (served since the
    # expiry-aware chain endpoint, 13 Aug 2026) over the 3rd-Friday estimate —
    # the estimate mislabels weeklies by up to two weeks, which distorts DTE
    # and therefore annualised yield.
    #
    # And pick the expiry CLOSEST TO target_dte among those listed, not
    # whichever weekly happens to dominate the legs: a month contains several
    # weeklies, so "nearest month" alone was selecting ~22 DTE contracts for a
    # 35-DTE target and then failing the screen's own 25-50 DTE gate
    # (live-caught on SLV, 13 Aug). Legs are filtered to the chosen expiry so
    # strike selection, greeks and premium all describe ONE contract.
    _mats = sorted({str(l.get("maturityDate")) for l in legs if l.get("maturityDate")})
    if expiry:
        expiry_iso = expiry
    elif _mats:
        def _iso(m: str) -> str:
            return f"{m[:4]}-{m[4:6]}-{m[6:]}"
        best = min(_mats, key=lambda m: abs((_dt.date.fromisoformat(_iso(m)) - today).days - target_dte))
        expiry_iso = _iso(best)
        legs = [l for l in legs if str(l.get("maturityDate")) == best] or legs
    else:
        expiry_iso = _month_to_date(chosen_month) or ""
    dte = max((_dt.date.fromisoformat(expiry_iso) - today).days, 1) if expiry_iso else target_dte
    _avail = data.get("availableExpiries") or []
    available = sorted({f"{m[:4]}-{m[4:6]}-{m[6:]}" for m in _avail
                        if isinstance(m, str) and len(m) == 8}) or None

    def to_quote(leg: dict) -> OptionQuote | None:
        strike = leg.get("strike")
        if strike is None:
            return None
        # NO FALSE POSITIVES: a leg with NO market data at all (bid, ask AND
        # delta all null — the cold-quote-cache / market-closed signature) is
        # DROPPED, not zero-filled. Coercing nulls to 0.0 here is exactly the
        # fabricated $0-premium / 0-IV quote the module contract forbids: a
        # $0 bid prices a CSP at zero yield and 0 IV breaks every vol-based
        # screen downstream. A leg with a real quote on ONE side (deep OTM
        # often has ask but no bid) keeps 0.0 on the silent side — that IS
        # the market saying "no bid", not missing data.
        bid, ask, delta = leg.get("bid"), leg.get("ask"), leg.get("delta")
        prior_close = leg.get("priorClose")
        if bid is None and ask is None and delta is None and prior_close is None:
            return None
        iv_pct = leg.get("impliedVolPct")
        return OptionQuote(
            kind="put" if leg.get("right") == "P" else "call",
            strike=float(strike),
            bid=float(bid or 0.0),
            ask=float(ask or 0.0),
            iv=(float(iv_pct) / 100.0) if iv_pct is not None else 0.0,
            # None stays None (source didn't serve OI — gate says "unavailable");
            # int() only on a REAL number. `or 0` here fabricated "OI 0 —
            # illiquid" on names with 20k+ true OI (AAPL, verified 9 Aug 2026).
            open_interest=(int(leg["openInterest"])
                           if leg.get("openInterest") is not None else None),
            prior_close=(float(prior_close) if prior_close is not None else None),
            delta=(float(delta) if delta is not None else None),
        )

    puts = [q for leg in legs if leg.get("right") == "P" and (q := to_quote(leg))]
    calls = [q for leg in legs if leg.get("right") == "C" and (q := to_quote(leg))]
    if not puts and not calls:
        log.warning(
            "%s: G3 chain had %d leg(s) but none carried quote data "
            "(bid/ask/delta all null — cold quote cache or market closed); "
            "returning None, not a fabricated chain", symbol, len(legs))
        return None

    # ── Quote capture (owner, 13 Aug 2026: "start storing data from now
    # on — this data will be quite key for our company"). IBKR serves no
    # history for expired contracts; the only honest future options
    # backtest runs on quotes captured while they were alive. ONE capture
    # point — every G3 consumer (screen, symbol lab, straddle scanner)
    # persists automatically. Fail-soft (a capture outage must never break
    # a screen) but NEVER silent: first failure per process logs a loud
    # warning + central run_log entry. Kill-switch: TRADEPRO_OPTION_QUOTE_CAPTURE=0.
    import os as _os
    if _os.environ.get("TRADEPRO_OPTION_QUOTE_CAPTURE", "1").strip().lower() not in ("0", "false", "no", "off"):
        try:
            rows = []
            for leg in legs:
                if leg.get("strike") is None or leg.get("right") not in ("P", "C"):
                    continue
                if leg.get("bid") is None and leg.get("ask") is None and leg.get("delta") is None:
                    continue    # quoteless leg — nothing worth storing
                _m = leg.get("maturityDate")
                leg_expiry = (f"{_m[:4]}-{_m[4:6]}-{_m[6:]}"
                              if isinstance(_m, str) and len(_m) == 8 else expiry_iso)
                if not leg_expiry:
                    continue
                iv_pct = leg.get("impliedVolPct")
                rows.append({
                    "symbol": symbol, "expiry": leg_expiry,
                    "strike": float(leg["strike"]), "right": leg["right"],
                    "bid": leg.get("bid"), "ask": leg.get("ask"),
                    "delta": leg.get("delta"),
                    "iv": (float(iv_pct) / 100.0) if iv_pct is not None else None,
                    "openInterest": (int(leg["openInterest"])
                                     if leg.get("openInterest") is not None else None),
                    "spot": float(spot), "source": "g3_chain",
                })
            if rows:
                requests.post(f"{base}/api/options/quotes-daily", json={"rows": rows},
                              headers={"Authorization": f"Bearer {token}"} if token else {},
                              timeout=15).raise_for_status()
        except Exception as e:  # noqa: BLE001 — fail-soft, never silent
            global _CAPTURE_WARNED
            if not _CAPTURE_WARNED:
                _CAPTURE_WARNED = True
                log.warning("option-quote capture FAILING (%s) — the future-backtest "
                            "dataset is not growing this run", e)
                try:
                    from ...run_log import log_run
                    log_run("option-quote-capture", "store", "warn",
                            error=f"capture failing: {e}")
                except Exception:  # noqa: BLE001
                    pass

    return OptionChain(
        symbol=symbol, spot=float(spot), expiry=expiry_iso or chosen_month, dte=dte,
        calls=calls, puts=puts, available_expiries=available,
    )


_CAPTURE_WARNED = False


__all__ = ["fetch_chain_g3"]
