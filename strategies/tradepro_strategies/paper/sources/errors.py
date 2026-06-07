"""DataUnavailableError — raised when ALL providers in the chain
return no bars for a symbol + date combination.

Contrast with individual BarSource implementations which return []
on per-source failure so FallbackSource can keep walking the chain.
This error is only raised when the FULL chain (all sources) has been
exhausted with nothing to show.

Strategy code should never catch this — it should propagate to the
bus / engine and be recorded as a per-symbol data error in the session
result, not silently absorbed."""
from __future__ import annotations
from datetime import datetime


class DataUnavailableError(Exception):
    """Raised by FallbackSource (and eventually BarStoreSource) when
    the full provider chain returns no bars for a symbol."""

    def __init__(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
        *,
        message: str = "",
    ) -> None:
        self.symbol = symbol
        self.session_date = session_date
        self.interval = interval
        msg = message or (
            f"no data for {symbol!r} on {session_date.date()} "
            f"at {interval!r} — all providers in chain returned empty"
        )
        super().__init__(msg)


__all__ = ["DataUnavailableError"]
