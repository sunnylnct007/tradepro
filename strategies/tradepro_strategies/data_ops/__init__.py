"""Trustworthy-data operations — handlers + registry + storage abstraction.

Phase C of the trustworthy data layer roadmap. This package holds the
business logic for every UI-triggerable data op (validate, backfill,
reload, repartition, purge). Adding a new op kind is one file:

    1. Subclass ``DataOpHandler`` in ``handlers/<kind>.py``.
    2. Decorate with ``@register_data_op("<kind_name>")``.
    3. Import the module from ``handlers/__init__.py`` so the
       decorator fires at package import.

The Mac-side ``tradepro-data-worker`` polling loop and any other
caller (an MCP tool, a backend test harness, a different worker
deployment) dispatch through ``dispatch(kind, request, storage)`` —
no special knowledge of which handler does what.

Design principles (per project memory):
  * Handler logic decoupled from transport (the polling loop lives in
    the CLI; the storage layer is injected). So a future deployment
    that runs the worker on a remote box reading from S3 doesn't
    require rewriting the handlers.
  * Service-boundary explicit. Handlers take a ``BarCacheStorage``;
    in production multi-service deployments, swap the implementation
    (``LocalBarCacheStorage`` today, ``S3BarCacheStorage`` Phase I).
  * Typed inputs / outputs via dataclasses. ``DataOpResult.ok=False``
    on handled errors so the worker doesn't have to interpret
    free-form dicts.
"""
from __future__ import annotations

from .registry import (
    DataOpHandler,
    DataOpRegistryError,
    dispatch,
    get_handler,
    list_kinds,
    register_data_op,
)
from .storage import BarCacheStorage, LocalBarCacheStorage
from .types import DataOpRequest, DataOpResult

# Import handlers to trigger @register_data_op side-effects. Same
# pattern as paper/strategies/__init__.py for strategy registration.
from . import handlers  # noqa: F401

__all__ = [
    "BarCacheStorage",
    "DataOpHandler",
    "DataOpRegistryError",
    "DataOpRequest",
    "DataOpResult",
    "LocalBarCacheStorage",
    "dispatch",
    "get_handler",
    "list_kinds",
    "register_data_op",
]
