"""IG epic mapping for paper-traded equity symbols.

Why this file exists:
  IG's REST API addresses instruments by `epic`, not ticker. The same
  symbol can have several epics (cash CFD vs DFB vs futures). A strategy
  cannot guess; an unmapped symbol must HARD-FAIL rather than silently
  trade the wrong listing. This loader is the single guardrail between
  a strategy emitting `symbol="SPY"` and the backend OMS submitting
  `epic="IX.D.SPXEFT.IFM.IP"` (or whatever the demo account exposes).

How to populate:
  Epic discovery is manual + per-listing. Against IG demo creds, hit
    GET /gateway/deal/markets?searchTerm=<symbol>
  and pick the epic whose `instrumentName` matches the listing you want
  to trade. Write the resulting `epic` string into `ig_epic_map.json`
  next to this module. Seed entries with `epic: null` are intentionally
  unusable until populated — a strategy attempting to route an order
  for an unmapped symbol will raise `IGEpicMissingError`.

Schema (ig_epic_map.json):
  {
    "SPY": {
      "epic": "IX.D.SPXEFT.IFM.IP",
      "instrument_name": "S&P 500 ETF (SPY)",
      "currency": "USD",
      "size_unit": "shares",
      "notes": "Cash CFD on the SPY US-listed ETF; demo + live both."
    },
    ...
  }

Design intent:
  - Read-only at runtime. No network calls in this module — discovery
    runs out-of-band; the loader just validates + indexes the JSON.
  - Fail-loud on missing epic. The cost of a wrong trade dwarfs the
    cost of an explicit error at session_start.
  - Typed at the boundary so a strategy can `epic_map.get(symbol).epic`
    and not worry about dict-shape drift.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class IGEpicMissingError(KeyError):
    """Raised when a strategy asks for an epic that isn't in the map or
    is in the map but has `epic: null` (i.e. not yet discovered against
    IG demo). Distinct from KeyError so callers can catch it
    specifically and emit a clearer audit log entry."""


@dataclass(frozen=True)
class IGEpicEntry:
    """One symbol → IG instrument mapping. `epic` is None until the
    operator has confirmed the epic against IG demo's `markets`
    endpoint; `IGEpicMap.get` refuses to return entries with epic=None
    so an unmapped symbol can never accidentally route."""
    symbol: str
    epic: str | None
    instrument_name: str | None = None
    currency: str | None = None
    size_unit: str | None = None
    notes: str | None = None


class IGEpicMap:
    """Read-through loader over the JSON file. Constructed once per
    process at startup (e.g. in the strategy's `on_session_start`),
    then queried per-symbol on order emission.

    Loader is strict: any malformed entry raises at load time so the
    failure is at boot, not mid-session."""

    def __init__(self, entries: dict[str, IGEpicEntry]) -> None:
        self._entries = entries

    @classmethod
    def load(cls, path: str | Path) -> "IGEpicMap":
        """Load + validate the JSON file. Missing file is an error —
        the caller is asking to route to IG, the map is required."""
        p = Path(path)
        with p.open("r", encoding="utf-8") as f:
            raw: dict[str, Any] = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(
                f"IG epic map at {p} must be a JSON object keyed by symbol"
            )
        entries: dict[str, IGEpicEntry] = {}
        for sym, body in raw.items():
            # Keys starting with `_` are reserved for inline documentation
            # (JSON has no comment syntax; `_comment` is the convention).
            if sym.startswith("_"):
                continue
            if not isinstance(body, dict):
                raise ValueError(
                    f"IG epic map entry for {sym!r} must be an object, "
                    f"got {type(body).__name__}"
                )
            entries[sym] = IGEpicEntry(
                symbol=sym,
                epic=body.get("epic"),  # may be None until discovered
                instrument_name=body.get("instrument_name"),
                currency=body.get("currency"),
                size_unit=body.get("size_unit"),
                notes=body.get("notes"),
            )
        return cls(entries)

    def get(self, symbol: str) -> IGEpicEntry:
        """Return the entry for `symbol`, raising IGEpicMissingError if
        the symbol isn't mapped or the epic hasn't been populated yet.
        Strategies should call this at order-emission time and skip
        the trade (with an audit-log entry) on failure rather than
        attempting to recover — the underlying issue is operator
        configuration, not transient."""
        entry = self._entries.get(symbol)
        if entry is None:
            raise IGEpicMissingError(
                f"{symbol!r} is not in the IG epic map — "
                f"add it via IG `markets?searchTerm={symbol}` "
                f"and write the epic into ig_epic_map.json"
            )
        if entry.epic is None:
            raise IGEpicMissingError(
                f"{symbol!r} is in the IG epic map but `epic` is null "
                f"— run epic discovery against IG demo and populate it"
            )
        return entry

    def mapped_symbols(self) -> list[str]:
        """All symbols with a populated epic. Useful to a scanner
        that wants to constrain its candidate universe to symbols the
        strategy can actually route."""
        return [s for s, e in self._entries.items() if e.epic is not None]

    def __contains__(self, symbol: str) -> bool:
        entry = self._entries.get(symbol)
        return entry is not None and entry.epic is not None

    def __len__(self) -> int:
        return sum(1 for e in self._entries.values() if e.epic is not None)


DEFAULT_MAP_PATH = Path(__file__).parent / "ig_epic_map.json"


__all__ = [
    "IGEpicEntry",
    "IGEpicMap",
    "IGEpicMissingError",
    "DEFAULT_MAP_PATH",
]
