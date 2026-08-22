"""UK index asset class plugin (^FTSE, ^FTMC, ...).

Sibling of ``index_us``, but on the LSE calendar — UK bank holidays differ
from NYSE's by roughly a week a year, so filing ^FTSE under a US calendar
would report phantom "missing sessions" every Easter and August bank
holiday. Subclasses ``UkEquityPlugin`` (LSE sessions, same OHLCV schema);
that plugin carries no flat-phantom guard, which is correct here — indices
print volume 0 on every bar by nature.

Context data, never tradeable: anything deriving a tradeable universe must
keep excluding ``^`` names.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..asset_class import register_asset_class
from .uk_equity import UkEquityPlugin


@dataclass
class IndexUkPlugin(UkEquityPlugin):
    """UK index plugin — LSE calendar, zero-volume bars are legitimate."""

    name: str = "index_uk"
    display_name: str = "UK index (context data)"


register_asset_class(IndexUkPlugin())

__all__ = ["IndexUkPlugin"]
