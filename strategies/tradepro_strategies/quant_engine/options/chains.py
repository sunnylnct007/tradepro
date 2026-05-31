"""Option-chain data model + a free (yfinance) adapter, with our own
Black-Scholes Greeks (the chain gives strike/bid/ask/IV but no delta).

PROVISIONAL data source — yfinance chains are delayed/patchy and IV can be
missing; Greeks here are model-derived, not market-verified. Swap to a paid
feed (ORATS) before any real reliance. See ROADMAP options workstream.

The strategy builders work against the OptionChain model, NOT yfinance —
so a different provider is just a new `fetch_chain`-shaped adapter.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

from .black_scholes import BlackScholesPricer, OptionType


@dataclass(frozen=True)
class OptionQuote:
    kind: OptionType            # "call" | "put"
    strike: float
    bid: float
    ask: float
    iv: float                   # implied vol (decimal); may be model-filled
    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.ask or self.bid
    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)


@dataclass(frozen=True)
class OptionChain:
    symbol: str
    spot: float
    expiry: str                 # YYYY-MM-DD
    dte: int                    # calendar days to expiry
    calls: list[OptionQuote]
    puts: list[OptionQuote]

    @property
    def t_years(self) -> float:
        return max(self.dte, 0) / 365.0

    def atm_strike(self) -> float:
        strikes = sorted({q.strike for q in (self.calls + self.puts)})
        return min(strikes, key=lambda k: abs(k - self.spot)) if strikes else self.spot


def delta_of(q: OptionQuote, spot: float, t_years: float, pricer: BlackScholesPricer) -> float:
    """Signed BS delta for a quote (put delta is negative)."""
    return pricer.greeks(spot, q.strike, t_years, max(q.iv, 1e-4), q.kind).delta


def select_by_abs_delta(
    quotes: list[OptionQuote], target_abs_delta: float, spot: float, t_years: float,
    pricer: BlackScholesPricer,
) -> OptionQuote | None:
    """Pick the quote whose |delta| is closest to the target (e.g. the
    '30-delta put' = |delta|≈0.30). Ignores quotes with no usable IV."""
    scored = [
        (abs(abs(delta_of(q, spot, t_years, pricer)) - target_abs_delta), q)
        for q in quotes if q.iv and q.iv > 0
    ]
    return min(scored, key=lambda x: x[0])[1] if scored else None


def fetch_chain(symbol: str, target_dte: int = 45, *, pricer: BlackScholesPricer | None = None) -> OptionChain | None:
    """yfinance adapter: pick the expiry nearest `target_dte` and return a
    normalised OptionChain. Fills a missing/zero IV by solving Black-Scholes
    from the mid so delta selection still works. Best-effort: returns None
    if the chain can't be read (no network / delisted / rate-limited)."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    pricer = pricer or BlackScholesPricer()
    try:
        tk = yf.Ticker(symbol)
        exps = tk.options
        if not exps:
            return None
        today = _dt.date.today()
        def dte(e: str) -> int:
            return (_dt.date.fromisoformat(e) - today).days
        # nearest expiry to target with at least a few DTE
        expiry = min((e for e in exps if dte(e) >= 1), key=lambda e: abs(dte(e) - target_dte), default=None)
        if expiry is None:
            return None
        spot = float(tk.fast_info.get("last_price") or tk.history(period="1d")["Close"].iloc[-1])
        ch = tk.option_chain(expiry)
        d = dte(expiry)
        t = max(d, 0) / 365.0

        def rows(df, kind: OptionType) -> list[OptionQuote]:
            out: list[OptionQuote] = []
            for _, r in df.iterrows():
                strike = float(r["strike"]); bid = float(r.get("bid") or 0); ask = float(r.get("ask") or 0)
                iv = float(r.get("impliedVolatility") or 0)
                mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (ask or bid)
                if (not iv or iv <= 0) and mid > 0 and t > 0:
                    solved = pricer.implied_vol(mid, spot, strike, t, kind)
                    iv = solved or 0.0
                if not (math.isfinite(strike) and strike > 0):
                    continue
                out.append(OptionQuote(kind=kind, strike=strike, bid=bid, ask=ask, iv=iv))
            return out

        return OptionChain(
            symbol=symbol, spot=spot, expiry=expiry, dte=d,
            calls=rows(ch.calls, "call"), puts=rows(ch.puts, "put"),
        )
    except Exception:  # noqa: BLE001 — adapter is best-effort
        return None


__all__ = ["OptionQuote", "OptionChain", "fetch_chain", "delta_of", "select_by_abs_delta"]
