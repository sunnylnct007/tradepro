"""Quiet yfinance's noisy "possibly delisted; no price data found" logs.

yfinance emits one ERROR per symbol per empty date range straight from its
own ``"yfinance"`` logger inside ``yf.download`` / ``Ticker.history``. A US
market HOLIDAY (no data that day) trips it for the WHOLE universe, so a
single daily warmup run floods the logs with hundreds of scary-but-benign
"possibly delisted" lines for active tickers (META, AAPL, …).

This installs a filter on the global ``"yfinance"`` logger that drops only
those no-data records. Real failures (rate limits, network) are unaffected —
our own provider code logs those at the right level with our own logger.

Temporary measure until the bar-cache serves history and we stop doing
per-day live yfinance fetches (see project_data_platform_dependency). Call
``quiet_yfinance_delisted_noise()`` at import of every module that fetches
via yfinance; it's idempotent and process-wide (one shared logger)."""
from __future__ import annotations

import logging

_QUIET_INSTALLED = False

# Lower-cased substrings that mark a benign "no data for this range" log.
# yfinance emits the error in two parts — a blank ERROR line + a
# "N Failed download:" preamble + the "$TICKER: possibly delisted ..."
# detail — so we drop all three shapes (and the bare blank line).
_NOISE_SUBSTRINGS = (
    "possibly delisted",
    "no price data found",
    "no price data",
    "failed download",
)


class _YFNoDataNoiseFilter(logging.Filter):
    """Drop benign no-data records; keep everything else yfinance logs."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage().strip().lower()
        except Exception:  # noqa: BLE001 — never let logging filtering raise
            return True
        if not msg:
            return False  # blank ERROR line — pure noise
        return not any(sub in msg for sub in _NOISE_SUBSTRINGS)


def quiet_yfinance_delisted_noise() -> None:
    """Install the no-data noise filter on the global ``yfinance`` logger.

    Idempotent: safe to call from every yfinance call-site module's import."""
    global _QUIET_INSTALLED
    if _QUIET_INSTALLED:
        return
    logging.getLogger("yfinance").addFilter(_YFNoDataNoiseFilter())
    _QUIET_INSTALLED = True


__all__ = ["quiet_yfinance_delisted_noise"]
