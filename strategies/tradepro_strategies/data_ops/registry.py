"""Handler registry + dispatch — same pattern as
``paper.registry.register_strategy`` and ``bar_cache.providers.register_provider``.

A handler is any callable with signature
``(request: DataOpRequest, storage: BarCacheStorage) -> DataOpResult``.
We accept both:
  * subclasses of ``DataOpHandler`` (recommended — gives the kind
    name as a class attribute + a clean __init__ hook)
  * plain functions (acceptable for trivial handlers)

The registry is process-local. A different worker process / a unit
test can ``register_data_op(kind)(func)`` to inject a synthetic
handler without touching the production registry the CLI uses.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, Union

from .storage import BarCacheStorage
from .types import DataOpRequest, DataOpResult


_log = logging.getLogger("tradepro.data_ops")


class DataOpHandler(ABC):
    """Base class for data op handlers. Subclass + decorate.

    Why a class rather than just a function: the class gives the
    handler somewhere to keep configuration that's lifecycle-bound
    (cache reader instances, rate-limit token buckets, logging
    prefixes, etc.). Today's handlers are stateless, but the option
    matters for backfill / repartition which will need throttling
    state."""

    # Set by @register_data_op. The kind name the backend session_requests
    # row carries. Used by ``dispatch`` to look up the handler.
    kind: str = ""

    @abstractmethod
    def handle(
        self, request: DataOpRequest, storage: BarCacheStorage,
    ) -> DataOpResult:
        """Execute the op. MUST return ``DataOpResult.ok=False`` for
        handled failures (operator-correctable). Should ``raise`` for
        truly unexpected exceptions — the worker catches at the
        polling-loop boundary and reports status='failed'."""


HandlerLike = Union[DataOpHandler, Callable[[DataOpRequest, BarCacheStorage], DataOpResult]]


class DataOpRegistryError(KeyError):
    """Raised when ``dispatch`` is asked for a kind that isn't
    registered. Distinct from a plain KeyError so the worker can
    pivot on it (the right action is "fail the request with a
    specific message", not "crash")."""


_REGISTRY: dict[str, HandlerLike] = {}


def register_data_op(kind: str) -> Callable[[type[DataOpHandler] | Callable], HandlerLike]:
    """Decorator. Use as::

        @register_data_op("data_validate")
        class ValidateHandler(DataOpHandler):
            ...

    Or for a function (the kind goes on the wrapper since plain
    functions don't have a ``kind`` attribute by default)::

        @register_data_op("data_purge_dry_run")
        def purge_dry_run(request, storage):
            ...
    """
    def deco(target):
        if isinstance(target, type) and issubclass(target, DataOpHandler):
            instance = target()
            instance.kind = kind
            _REGISTRY[kind] = instance
            return target
        # Plain callable.
        _REGISTRY[kind] = target
        return target
    return deco


def get_handler(kind: str) -> HandlerLike:
    if kind not in _REGISTRY:
        raise DataOpRegistryError(
            f"no handler registered for kind={kind!r}; "
            f"registered: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[kind]


def list_kinds() -> list[str]:
    """Names of every registered op kind. The CLI logs this at
    startup so an operator can confirm "the worker knows about
    data_validate + data_backfill" before traffic arrives."""
    return sorted(_REGISTRY.keys())


def dispatch(
    request: DataOpRequest, storage: BarCacheStorage,
) -> DataOpResult:
    """Look up the handler for ``request.kind`` and run it. Wraps
    ``DataOpRegistryError`` into a structured ``DataOpResult`` so the
    worker doesn't have to special-case 'no handler' vs 'handler
    raised'."""
    try:
        handler = get_handler(request.kind)
    except DataOpRegistryError as exc:
        return DataOpResult(
            ok=False,
            summary=f"no handler for kind={request.kind!r}",
            error=str(exc),
        )
    if isinstance(handler, DataOpHandler):
        return handler.handle(request, storage)
    return handler(request, storage)


def _clear_registry_for_tests() -> None:
    _REGISTRY.clear()
