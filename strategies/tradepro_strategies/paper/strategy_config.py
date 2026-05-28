"""StrategyConfigRegistry — persistent per-strategy configuration store.

Stores and retrieves the full parameter set for each registered strategy,
including LLM gate settings. Backed by JSON at ~/.tradepro/strategy_configs.json.

The UI calls MCP tools which call this registry to read/write config.
Strategies read their config at session_start via get_config(name).

Concurrency: same RLock + atomic-rename write pattern as OverrideRegistry.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm_gate import LLMGateConfig


DEFAULT_CONFIG_PATH = Path.home() / ".tradepro" / "strategy_configs.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StrategyConfig:
    """Full per-strategy configuration record.

    `params` holds strategy-specific kwargs (sleeve_size, symbols, …).
    `llm_gate` is a serialised LLMGateConfig (use the gate's to_dict/
    from_dict to round-trip). `enabled` is a master switch separate
    from the session-scoped PAUSE override.
    """
    strategy_name: str
    params: dict[str, Any] = field(default_factory=dict)
    llm_gate: dict[str, Any] = field(default_factory=lambda: LLMGateConfig().to_dict())
    enabled: bool = True
    updated_at: str = field(default_factory=_now_iso)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategyConfig":
        return cls(
            strategy_name=d["strategy_name"],
            params=dict(d.get("params") or {}),
            llm_gate=dict(d.get("llm_gate") or LLMGateConfig().to_dict()),
            enabled=bool(d.get("enabled", True)),
            updated_at=str(d.get("updated_at") or _now_iso()),
            notes=str(d.get("notes") or ""),
        )


class StrategyConfigRegistry:
    """Thread-safe JSON-backed per-strategy config store.

    The UI is the primary writer (via MCP tools); the StrategyRunner is
    the primary reader at session-start. Atomic-rename on write so a
    crashed writer can't leave a half-truncated file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        self._lock = threading.RLock()
        self._configs: dict[str, StrategyConfig] = {}
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
            return
        items = raw.get("configs", []) if isinstance(raw, dict) else raw
        with self._lock:
            self._configs = {}
            for d in items:
                try:
                    cfg = StrategyConfig.from_dict(d)
                except (KeyError, TypeError, ValueError):
                    continue
                self._configs[cfg.strategy_name] = cfg

    def _persist(self) -> None:
        # Caller MUST already hold self._lock.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "configs": [c.to_dict() for c in self._configs.values()],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    def get(self, strategy_name: str) -> StrategyConfig:
        """Return the stored config or a fresh default (enabled, no params,
        default LLM gate). Never raises for unknown strategies — callers
        always get a usable object."""
        with self._lock:
            existing = self._configs.get(strategy_name)
            if existing is not None:
                # Return a shallow copy so callers can mutate freely.
                return StrategyConfig.from_dict(existing.to_dict())
            return StrategyConfig(
                strategy_name=strategy_name,
                params={},
                llm_gate=LLMGateConfig().to_dict(),
                enabled=True,
            )

    def all_configs(self) -> list[StrategyConfig]:
        """All stored configs. Empty list if nothing has been written."""
        with self._lock:
            return [
                StrategyConfig.from_dict(c.to_dict())
                for c in self._configs.values()
            ]

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    def set(self, config: StrategyConfig) -> None:
        """Persist (or overwrite) a full config record."""
        with self._lock:
            config.updated_at = _now_iso()
            self._configs[config.strategy_name] = config
            self._persist()

    def update_params(self, strategy_name: str, params: dict[str, Any]) -> StrategyConfig:
        """Merge-update params — existing keys preserved, new keys added.

        Returns the post-update record for ergonomics (UI doesn't need a
        second .get() call to render the result)."""
        with self._lock:
            existing = self._configs.get(strategy_name) or StrategyConfig(
                strategy_name=strategy_name,
                llm_gate=LLMGateConfig().to_dict(),
            )
            merged = dict(existing.params)
            merged.update(params or {})
            existing.params = merged
            existing.updated_at = _now_iso()
            self._configs[strategy_name] = existing
            self._persist()
            return StrategyConfig.from_dict(existing.to_dict())

    def update_llm_gate(
        self,
        strategy_name: str,
        gate_config: LLMGateConfig,
    ) -> StrategyConfig:
        """Replace the LLM gate config for one strategy."""
        with self._lock:
            existing = self._configs.get(strategy_name) or StrategyConfig(
                strategy_name=strategy_name,
                llm_gate=LLMGateConfig().to_dict(),
            )
            existing.llm_gate = gate_config.to_dict()
            existing.updated_at = _now_iso()
            self._configs[strategy_name] = existing
            self._persist()
            return StrategyConfig.from_dict(existing.to_dict())

    def set_enabled(self, strategy_name: str, enabled: bool) -> StrategyConfig:
        """Flip the master enable switch for one strategy."""
        with self._lock:
            existing = self._configs.get(strategy_name) or StrategyConfig(
                strategy_name=strategy_name,
                llm_gate=LLMGateConfig().to_dict(),
            )
            existing.enabled = bool(enabled)
            existing.updated_at = _now_iso()
            self._configs[strategy_name] = existing
            self._persist()
            return StrategyConfig.from_dict(existing.to_dict())

    # ------------------------------------------------------------------ #
    # UI projection                                                        #
    # ------------------------------------------------------------------ #

    def to_status_dict(
        self,
        strategy_name: str,
        override_registry: Any = None,
    ) -> dict[str, Any]:
        """Status view for the UI / MCP — combines stored config with
        the live PAUSE state from the override registry."""
        cfg = self.get(strategy_name)
        paused = False
        if override_registry is not None:
            try:
                paused = bool(override_registry.is_paused(strategy_name))
            except Exception:  # noqa: BLE001
                paused = False
        return {
            "strategy_name": cfg.strategy_name,
            "enabled": cfg.enabled,
            "paused": paused,
            "params": cfg.params,
            "llm_gate": cfg.llm_gate,
            "updated_at": cfg.updated_at,
            "notes": cfg.notes,
        }


__all__ = [
    "StrategyConfig",
    "StrategyConfigRegistry",
    "DEFAULT_CONFIG_PATH",
]
