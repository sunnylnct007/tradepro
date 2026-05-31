"""Concrete handlers — import here to fire the registration
decorators at package import time.

Same pattern paper/strategies/__init__.py uses for strategies +
bar_cache/asset_classes/__init__.py uses for plugins.

When a new handler lands, add one line here. The CLI / worker doesn't
need any change — ``dispatch(kind, ...)`` looks the new handler up by
its registered name.
"""
from __future__ import annotations

from .validate import ValidateHandler  # noqa: F401 — registers

__all__ = ["ValidateHandler"]
