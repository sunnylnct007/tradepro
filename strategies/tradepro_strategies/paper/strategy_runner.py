"""StrategyRunner — high-level façade that assembles Engine + strategies
from stored config and runs a paper session with minimal boilerplate.

The trader (or a cron job) calls `StrategyRunner.run_session()` to:
  1. Load strategy configs from StrategyConfigRegistry
  2. Skip disabled/paused strategies
  3. Build LLMSignalGate per strategy (if enabled in config)
  4. Wire strategies into the paper Engine
  5. Run the session
  6. Return a summary dict

This is the "minimize human effort" entry point. Zero config needed
after initial setup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .llm_gate import LLMGateConfig, LLMSignalGate
from .overrides import OverrideRegistry
from .strategy_config import StrategyConfigRegistry


@dataclass
class SessionSummary:
    """Result of one run_session() invocation. Surfaced to the UI / cron
    log so the operator can see what fired today vs what was skipped
    and why, without trawling the engine event log."""
    date: str
    strategies_run: list[str] = field(default_factory=list)
    strategies_skipped: list[str] = field(default_factory=list)
    total_orders: int = 0
    total_fills: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "strategies_run": list(self.strategies_run),
            "strategies_skipped": list(self.strategies_skipped),
            "total_orders": self.total_orders,
            "total_fills": self.total_fills,
            "errors": list(self.errors),
        }


class StrategyRunner:
    """Façade over StrategyConfigRegistry + OverrideRegistry + paper Engine.

    A cron job calls `run_session()` once per trading day; the runner
    queries both registries to decide what to run, builds an LLMSignalGate
    per strategy (where configured), wires everything into the paper Engine,
    and returns a summary.

    The Engine wiring itself requires a live BarBus — out of scope for the
    unit tests. The status/build/select helpers are independently testable.
    """

    def __init__(
        self,
        config_registry: StrategyConfigRegistry,
        override_registry: OverrideRegistry,
        broker: str = "t212",
        broker_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.config_registry = config_registry
        self.override_registry = override_registry
        self.broker = broker
        self.broker_kwargs = dict(broker_kwargs or {})

    # ------------------------------------------------------------------ #
    # Selection                                                            #
    # ------------------------------------------------------------------ #

    def get_active_strategies(self) -> list[str]:
        """Strategies that are enabled in the config AND not paused via
        an OverrideRegistry PAUSE. Order: alphabetical by name (stable
        across runs so the cron log diffs cleanly)."""
        active: list[str] = []
        for cfg in self.config_registry.all_configs():
            if not cfg.enabled:
                continue
            try:
                if self.override_registry.is_paused(cfg.strategy_name):
                    continue
            except Exception:  # noqa: BLE001
                # A failing override registry shouldn't silently drop
                # strategies — surface them as active and let the strategy
                # itself decide what to do on the next bar.
                pass
            active.append(cfg.strategy_name)
        return sorted(active)

    # ------------------------------------------------------------------ #
    # Build                                                                #
    # ------------------------------------------------------------------ #

    def build_strategy(self, name: str):
        """Instantiate a strategy from its stored params via the paper
        registry. Returns None for unknown strategy names — the runner
        treats "not registered" as "skip", not as an error, so a stale
        config row for a removed strategy doesn't crash the whole session.
        """
        from .registry import get as registry_get

        try:
            spec = registry_get(name)
        except KeyError:
            return None
        cfg = self.config_registry.get(name)
        try:
            return spec.build(strategy_id=name, params=cfg.params or {})
        except Exception:  # noqa: BLE001
            return None

    def build_llm_gate(self, name: str) -> LLMSignalGate | None:
        """Build the LLM gate for a strategy. None if gate disabled in
        the strategy's stored config (the runner skips gating entirely
        in that case rather than instantiating a pass-through gate)."""
        cfg = self.config_registry.get(name)
        try:
            gate_cfg = LLMGateConfig.from_dict(cfg.llm_gate or {})
        except (TypeError, ValueError):
            return None
        if not gate_cfg.enabled:
            return None
        return LLMSignalGate(gate_cfg)

    # ------------------------------------------------------------------ #
    # Status                                                               #
    # ------------------------------------------------------------------ #

    def status(self) -> dict[str, Any]:
        """Status of every configured strategy — for the UI overview page
        and the daily cron summary mail. One row per strategy:
            { strategy_name, enabled, paused, llm_gate_enabled,
              last_config_update }
        """
        rows: list[dict[str, Any]] = []
        for cfg in self.config_registry.all_configs():
            try:
                paused = bool(self.override_registry.is_paused(cfg.strategy_name))
            except Exception:  # noqa: BLE001
                paused = False
            gate_cfg = cfg.llm_gate or {}
            llm_enabled = bool(gate_cfg.get("enabled", False))
            rows.append({
                "strategy_name": cfg.strategy_name,
                "enabled": cfg.enabled,
                "paused": paused,
                "llm_gate_enabled": llm_enabled,
                "last_config_update": cfg.updated_at,
            })
        return {
            "strategies": rows,
            "count": len(rows),
            "active_count": len(self.get_active_strategies()),
        }

    # ------------------------------------------------------------------ #
    # Run (placeholder — requires a live BarBus)                           #
    # ------------------------------------------------------------------ #

    def run_session(self, *args: Any, **kwargs: Any) -> SessionSummary:
        """Run one paper trading session for all active strategies.

        NOT implemented in v1: this entry point requires a live BarBus
        and broker connection, which is out of scope for the unit tests.
        Once the cron wiring lands, the body will:
          1. Resolve active strategies via get_active_strategies()
          2. Build each via build_strategy()
          3. Build the LLM gate (if any) via build_llm_gate()
          4. Wire them into Engine + BarBus + broker
          5. Engine.run() until session_end
          6. Collect order/fill counts into SessionSummary
        """
        raise NotImplementedError(
            "StrategyRunner.run_session requires a live BarBus + broker; "
            "stub for v1 — call get_active_strategies / build_strategy / "
            "build_llm_gate from your own driver until the cron wiring lands."
        )


__all__ = ["StrategyRunner", "SessionSummary"]
