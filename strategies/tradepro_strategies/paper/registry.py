"""Strategy registry — discoverable plug-in catalogue.

The contract for "anyone can write a new strategy":

  1. Subclass `Strategy` (the ABC in `paper/strategy.py`) and
     implement `on_bar`. Optionally override `on_session_start`,
     `on_fill`, `on_session_end`.
  2. Register via ONE of three paths:
     (a) `@register_strategy("my_name")` decorator above the class
         definition. Best for in-tree strategies.
     (b) setuptools entry point in your package's pyproject.toml:
             [project.entry-points."tradepro.strategies"]
             my_name = "my_pkg.module:MyStrategy"
         Best for strategies shipped as separate pip packages.
     (c) Dotted-path on the CLI: `--strategy-class my_pkg.mod:MyClass`.
         Best for ad-hoc experiments / one-off scripts.
  3. The CLI accepts `--strategy <name>` (lookup) or
     `--strategy-class <dotted>` (dynamic import). Both reach the
     same `WalkForwardValidator` underneath — the registry is just
     a name→class indirection.

Why this lives in its own module rather than inside `strategies/`:
  - Avoids a circular import between the ABC + concrete strategies
    + the discovery code.
  - Entry-point loading should run exactly once per process; gating
    that with a module-level cache lives cleanly here.

Future: the same registry will index Risk profiles + Bar sources
so an operator can build a session declaratively from a YAML config
without writing Python wiring. Out of scope for v1 — but the namespace
is reserved (`tradepro.strategies`, future `tradepro.risk_profiles`,
`tradepro.bar_sources`).
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Callable

from .strategy import Strategy


log = logging.getLogger("tradepro.paper.registry")


_REGISTRY: dict[str, type[Strategy]] = {}
_ENTRY_POINTS_LOADED = False

#: Strategies RETIRED by decision. Registration is refused for these names, so
#: importing the module cannot bring one back.
#:
#: WHY THIS EXISTS, 13 Oct 2026. Retirement used to mean "do not import it in
#: paper/strategies/__init__.py", on the reasoning that @register_strategy runs
#: at class definition so not importing is the whole deregistration. That is
#: true and it is not a mechanism: ANY other module importing the class
#: re-registers it, from anywhere, silently.
#:
#: Which is what happened. The 10 Oct cut removed the four imports from
#: paper/strategies/__init__.py and left them in cli/paper_session.py — the
#: LANE ENTRY POINT, imported by every live lane. Measured:
#:
#:     import tradepro_strategies.paper.strategies   -> [swing, momentum]
#:     import tradepro_strategies.cli.paper_session  -> [swing, momentum, orb]
#:
#: So orb was registered in every lane process for three days while the module
#: comment said it was retired, and it reached the live API's strategy
#: catalogue. One fix, two sites, landed at one — the shape that keeps costing
#: this desk days.
#:
#: Retirement is now a DECLARATION the registry enforces rather than an absence
#: nobody can verify. Importing a retired class is still fine (its backtests
#: and tests construct it directly); what it can no longer do is make the
#: strategy runnable.
#:
#: To bring one back: remove it HERE, and schedule it. Deliberately one line,
#: because a retired strategy returning should be a decision, not an accident.
RETIRED: frozenset[str] = frozenset({
    "ichimoku_equity",    # last order 23 Sep 2026; nothing schedules it
    "ichimoku_fx_mr",     # IG forex; IG no longer in use
    "intraday_flat",      # last order ~June 2026
    "orb",                # failed 3 of 5 pre-registered gates
                          # (INTRADAY_VWAP_ORB_GATES_V1); its 1m parquet feed
                          # has written nothing since early Oct
})


@dataclass
class StrategySpec:
    """How to instantiate a strategy at runtime. Used by the CLI +
    comparator + (future) UI form. Trivial today; gives us a hook to
    add param-schema validation later without changing call sites."""
    name: str
    cls: type[Strategy]

    def build(
        self,
        strategy_id: str,
        params: dict[str, Any] | None = None,
        risk: Any = None,
    ) -> Strategy:
        return self.cls(strategy_id=strategy_id, params=params or {}, risk=risk)

    def default_params(self) -> dict[str, Any]:
        """Pulls `default_params` from the class if defined. Strategies
        that don't expose one return {} — the CLI can still pass an
        empty params dict and rely on the strategy's __init__ defaults."""
        fn = getattr(self.cls, "default_params", None)
        if fn is None:
            return {}
        try:
            return dict(fn())
        except TypeError:
            # default_params might be an instance method; allow that too
            return dict(fn(self.cls))


def register_strategy(name: str) -> Callable[[type[Strategy]], type[Strategy]]:
    """Decorator. Usage:

        @register_strategy("orb")
        class OpeningRangeBreakout(Strategy):
            ...

    Re-registration under the same name raises rather than silently
    overriding — name collisions are almost always a bug."""
    def decorator(cls: type[Strategy]) -> type[Strategy]:
        if not issubclass(cls, Strategy):
            raise TypeError(
                f"register_strategy({name!r}): {cls!r} is not a Strategy subclass"
            )
        # RETIRED NAMES DO NOT REGISTER, whoever imported the module. Skipped
        # rather than raised: importing a retired class is legitimate (its own
        # tests and backtests construct it directly), and an exception here
        # would turn `import cli.paper_session` — the live lane entry point —
        # into a crash. Logged at INFO so the refusal is visible rather than
        # mysterious; see RETIRED above for why absence-of-import was not
        # enough.
        if name in RETIRED:
            log.info(
                "register_strategy(%r): RETIRED — not registering. %s imported "
                "the class, which under the old 'just do not import it' rule "
                "would have silently brought it back.", name, cls.__module__)
            return cls
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(
                f"strategy name {name!r} already registered to {_REGISTRY[name]!r}"
            )
        _REGISTRY[name] = cls
        return cls
    return decorator


def get(name: str) -> StrategySpec:
    """Look up a registered strategy by name. Loads entry-point
    packages on first miss so third-party strategies don't have to
    be explicitly imported before lookup works."""
    if name not in _REGISTRY:
        _load_entry_points()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy {name!r}. "
            f"Registered: {sorted(list_names())}. "
            f"To use a non-registered class, pass --strategy-class <module:Class>."
        )
    return StrategySpec(name=name, cls=_REGISTRY[name])


def from_dotted(path: str) -> StrategySpec:
    """`my_pkg.module:ClassName` → StrategySpec via dynamic import.
    Useful for ad-hoc strategies that live in any Python file on
    PYTHONPATH — no registry entry, no entry point, no decorator
    required. The class still has to subclass `Strategy`."""
    if ":" not in path:
        raise ValueError(f"--strategy-class expects 'module:Class', got {path!r}")
    module_name, _, class_name = path.partition(":")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(
            f"module {module_name} has no attribute {class_name}"
        )
    if not isinstance(cls, type) or not issubclass(cls, Strategy):
        raise TypeError(f"{path!r} did not resolve to a Strategy subclass")
    return StrategySpec(name=f"adhoc:{path}", cls=cls)


def list_names() -> list[str]:
    """All registered strategy names (in-tree + entry-point). Sorted
    so CLI `--list-strategies` output is stable."""
    _load_entry_points()
    return sorted(_REGISTRY)


def all_specs() -> list[StrategySpec]:
    return [StrategySpec(name=n, cls=c) for n, c in sorted(_REGISTRY.items())]


def _load_entry_points() -> None:
    """Discover third-party strategies shipped as pip packages that
    declare a `tradepro.strategies` entry point. Runs once per process."""
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return
    try:
        eps = entry_points(group="tradepro.strategies")
    except TypeError:
        # Older importlib_metadata API: entry_points() returns a dict
        eps = entry_points().get("tradepro.strategies", [])  # type: ignore
    for ep in eps:
        try:
            cls = ep.load()
        except Exception:
            log.exception("entry-point %s failed to load", ep.name)
            continue
        if not isinstance(cls, type) or not issubclass(cls, Strategy):
            log.warning(
                "entry-point %s resolved to %r which is not a Strategy subclass",
                ep.name, cls,
            )
            continue
        if ep.name in _REGISTRY and _REGISTRY[ep.name] is not cls:
            log.warning(
                "entry-point %s collides with existing %r; keeping in-tree class",
                ep.name, _REGISTRY[ep.name],
            )
            continue
        _REGISTRY[ep.name] = cls


__all__ = [
    "StrategySpec",
    "register_strategy",
    "get",
    "from_dotted",
    "list_names",
    "all_specs",
]
