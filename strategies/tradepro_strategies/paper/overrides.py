"""Manual override registry for paper trading strategies.

The trader can:
  - PAUSE / RESUME a whole strategy (no new signals while paused)
  - VETO a pending order before it reaches the broker (one-shot, consumed on read)
  - PRICE_OVERRIDE — convert MARKET->LIMIT at specified price (one-shot)
  - SIZE_OVERRIDE — change quantity before submission (one-shot)
  - FORCE_CLOSE — immediately flatten a position (one-shot)

All overrides are persisted to JSON so they survive process restarts.
One-shot overrides (VETO, PRICE, SIZE, FORCE_CLOSE) are consumed on first read.
PAUSE/RESUME are persistent state — PAUSE stays until RESUME replaces it.

Why this lives outside any individual strategy: every strategy needs the
same trader-override surface, and the trader's UI talks to ONE registry.
A strategy-local "is_paused" flag wouldn't survive restarts and wouldn't
have a single audit trail. JSON-on-disk gives both for free.

Concurrency: a coarse-grained threading.RLock guards the whole list +
file write. Overrides are low-frequency (trader clicks at human speed,
strategies read on each bar tick at most a few times per second per
strategy), so contention is a non-issue and a single lock keeps the
reasoning simple.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


DEFAULT_OVERRIDES_PATH = Path.home() / ".tradepro" / "paper_overrides.json"


class OverrideAction(str, Enum):
    """All trader-initiated override actions.

    PAUSE/RESUME are persistent: the most-recent one wins until replaced.
    Everything else is one-shot: applied at most once, then consumed.
    """
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    VETO_ORDER = "VETO_ORDER"
    PRICE_OVERRIDE = "PRICE_OVERRIDE"
    SIZE_OVERRIDE = "SIZE_OVERRIDE"
    FORCE_CLOSE = "FORCE_CLOSE"


@dataclass
class StrategyOverride:
    """One trader-initiated override request.

    `symbol=None` means "any symbol" — useful for PAUSE/RESUME which
    flip a whole strategy. One-shot symbol-bound overrides match by
    exact symbol equality (with `None` acting as a wildcard on read).
    """
    strategy_name: str
    action: OverrideAction
    symbol: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategyOverride":
        return cls(
            strategy_name=d["strategy_name"],
            action=OverrideAction(d["action"]),
            symbol=d.get("symbol"),
            params=dict(d.get("params") or {}),
            created_at=d.get("created_at")
                or datetime.now(timezone.utc).isoformat(),
            note=d.get("note", ""),
        )


class OverrideRegistry:
    """Thread-safe, JSON-backed override store.

    One process should hold one OverrideRegistry instance (one file).
    Pass it into strategies as a constructor param or via params dict;
    do NOT spin up multiple registries pointing at the same path or
    you'll race on the JSON file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path = Path(path) if path is not None else DEFAULT_OVERRIDES_PATH
        self._lock = threading.RLock()
        self._overrides: list[StrategyOverride] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            # Corrupt file → start clean rather than crash the engine.
            return
        items = raw.get("overrides", []) if isinstance(raw, dict) else raw
        with self._lock:
            self._overrides = [StrategyOverride.from_dict(d) for d in items]

    def _persist(self) -> None:
        # Caller MUST already hold self._lock.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"overrides": [o.to_dict() for o in self._overrides]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # ------------------------------------------------------------------ #
    # Mutation                                                             #
    # ------------------------------------------------------------------ #

    def apply(self, override: StrategyOverride) -> None:
        """Append override, persist atomically."""
        with self._lock:
            self._overrides.append(override)
            self._persist()

    def clear(self, strategy_name: str) -> None:
        """Drop every override for `strategy_name` (any action, any symbol)."""
        with self._lock:
            self._overrides = [
                o for o in self._overrides if o.strategy_name != strategy_name
            ]
            self._persist()

    # ------------------------------------------------------------------ #
    # Persistent-state queries                                            #
    # ------------------------------------------------------------------ #

    def is_paused(self, strategy_name: str) -> bool:
        """True iff the most-recent PAUSE/RESUME for this strategy is PAUSE.

        Walks the list in reverse so the latest record wins. RESUME wipes
        any earlier PAUSE; absence of either action = not paused.
        """
        with self._lock:
            for o in reversed(self._overrides):
                if o.strategy_name != strategy_name:
                    continue
                if o.action == OverrideAction.PAUSE:
                    return True
                if o.action == OverrideAction.RESUME:
                    return False
            return False

    def all_overrides(self, strategy_name: str) -> list[StrategyOverride]:
        """Snapshot of every override currently in the registry for this
        strategy (does NOT consume one-shots). For UI display / debugging."""
        with self._lock:
            return [o for o in self._overrides if o.strategy_name == strategy_name]

    # ------------------------------------------------------------------ #
    # One-shot queries (CONSUMING — first reader wins)                    #
    # ------------------------------------------------------------------ #

    def _pop_first_matching(
        self,
        strategy_name: str,
        action: OverrideAction,
        symbol: str | None,
    ) -> StrategyOverride | None:
        """Find + remove the OLDEST matching override. None if no match.

        Symbol matching rule: an override with `symbol=None` is a wildcard
        and matches any caller-supplied symbol. An override with a specific
        symbol matches only that symbol. Caller passing `symbol=None`
        matches any override of this action (used by all_overrides probes).
        """
        # Caller MUST already hold self._lock.
        for i, o in enumerate(self._overrides):
            if o.strategy_name != strategy_name or o.action != action:
                continue
            if symbol is not None and o.symbol is not None and o.symbol != symbol:
                continue
            del self._overrides[i]
            self._persist()
            return o
        return None

    def get_price_override(
        self,
        strategy_name: str,
        symbol: str | None = None,
    ) -> float | None:
        """Consume a PRICE_OVERRIDE; returns the price float (params['price']).

        Subsequent calls for the same strategy/symbol get None unless another
        override is registered."""
        with self._lock:
            o = self._pop_first_matching(
                strategy_name, OverrideAction.PRICE_OVERRIDE, symbol
            )
            if o is None:
                return None
            price = o.params.get("price")
            try:
                return float(price) if price is not None else None
            except (TypeError, ValueError):
                return None

    def get_size_override(
        self,
        strategy_name: str,
        symbol: str | None = None,
    ) -> int | None:
        """Consume a SIZE_OVERRIDE; returns the size int (params['quantity'])."""
        with self._lock:
            o = self._pop_first_matching(
                strategy_name, OverrideAction.SIZE_OVERRIDE, symbol
            )
            if o is None:
                return None
            qty = o.params.get("quantity")
            try:
                return int(qty) if qty is not None else None
            except (TypeError, ValueError):
                return None

    def consume_veto(self, strategy_name: str, symbol: str | None = None) -> bool:
        """Consume a VETO_ORDER. True if one was waiting and just got eaten."""
        with self._lock:
            o = self._pop_first_matching(
                strategy_name, OverrideAction.VETO_ORDER, symbol
            )
            return o is not None

    def consume_force_close(
        self, strategy_name: str, symbol: str | None = None
    ) -> bool:
        """Consume a FORCE_CLOSE. True if one was waiting."""
        with self._lock:
            o = self._pop_first_matching(
                strategy_name, OverrideAction.FORCE_CLOSE, symbol
            )
            return o is not None


__all__ = [
    "OverrideAction",
    "StrategyOverride",
    "OverrideRegistry",
    "DEFAULT_OVERRIDES_PATH",
]
