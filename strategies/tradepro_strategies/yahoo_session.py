"""The ONE way to build a requests session for yfinance.

Two things must both be true and they pull against each other, which is why
this needs a single owner rather than a copy per caller.

**A timeout is mandatory.** Without one, `ticker.history()` blocks on a socket
read with no deadline. On 15 Aug 2026 that turned a single day's options-screen
run into 2h50m: 53 of 82 symbols fell through to yfinance, Yahoo was refusing
service, and each throttled symbol simply hung. A fallback with no time bound
is not a fallback, it is a hang.

**Impersonation is equally mandatory**, and this is the part that got missed.
yfinance's own default session impersonates a browser TLS fingerprint. Handing
it a bare `curl_cffi.Session` REPLACES that with a plain client, and Yahoo's bot
detection answers a non-browser fingerprint with 429 "Too Many Requests" —
indistinguishable from a genuine throttle. The project spent weeks waiting for a
Yahoo rate limit to "clear" that was entirely self-inflicted and was never going
to clear. Measured 23 Aug, same process, back to back:

    no session                                -> 5 rows
    Session(timeout=8)                        -> YFRateLimitError
    Session(timeout=8, impersonate="chrome")  -> 5 rows

Fixing one caller and not the other is how this file came to exist. The bar
provider was fixed on 23 Aug; `quant_engine/options/chains.py` kept its own copy
without impersonation and produced **158 YFRateLimitError warnings on 24 Aug
alone** — a fix that did not propagate is barely a fix, and the second site was
named in the very memory describing the first.

Never trade the timeout for the impersonation or the reverse. If curl_cffi is
too old to impersonate, this returns None so yfinance uses its own (browser-
impersonating) session — a session that gets 429'd on every call is worse than
no session at all.
"""
from __future__ import annotations

import functools
import logging
import os

_log = logging.getLogger("tradepro.yahoo_session")

_SESSION: object | None = None
_BUILT = False


def yahoo_session(timeout_s: float | None = None):
    """A yfinance-safe session: real timeout AND browser impersonation.

    Cached process-wide — building one per call defeats connection reuse and
    makes the throttle worse. Returns None when no safe session can be built,
    which callers must pass through to yfinance as "no session".
    """
    global _SESSION, _BUILT
    if _BUILT:
        return _SESSION

    timeout = float(
        timeout_s if timeout_s is not None
        else os.environ.get("TRADEPRO_YF_TIMEOUT_S", "8"))

    session = None
    try:
        from curl_cffi import requests as _cr
        try:
            session = _cr.Session(timeout=timeout, impersonate="chrome")
        except TypeError:
            _log.warning(
                "yfinance: installed curl_cffi has no impersonate= support; "
                "falling back to yfinance's own session so Yahoo does not 429 "
                "us. TRADEPRO_YF_TIMEOUT_S will not apply — upgrade curl_cffi.")
            session = None
    except Exception:  # noqa: BLE001 — curl_cffi absent entirely
        try:
            import requests as _rq
            s = _rq.Session()
            # plain requests carries no TLS fingerprint worth spoofing; the
            # timeout is the only thing this buys us.
            s.request = functools.partial(s.request, timeout=timeout)  # type: ignore[method-assign]
            session = s
        except Exception:  # noqa: BLE001
            session = None

    _SESSION, _BUILT = session, True
    return _SESSION


def reset_for_tests() -> None:
    global _SESSION, _BUILT
    _SESSION, _BUILT = None, False
