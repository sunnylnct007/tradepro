"""Read-only client for the TradePro API's /api/settings endpoint.

The Mac engine uses this to discover runtime configuration that the
user might have flipped from the UI (e.g. paper-trading placement
mode) without redeploying or re-running with a new CLI flag.

Lookup chain mirrors ``tradepro_strategies.secrets.get_secret``:

  1. Read api-base-url + api-token via ``get_secret`` (env → AWS SM
     → ~/.tradepro/credentials).
  2. ``GET /api/settings`` with the bearer token.
  3. Return the parsed JSON. Best-effort — any failure (no creds,
     API unreachable, malformed response) returns ``None`` so the
     caller can fall back to compiled defaults.

Cached per-process. Use ``clear_cache()`` when the same Python
worker needs to pick up a fresh value during a long-running session
(e.g. a daemonised launchd job after a settings change).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("tradepro.api_settings")

_CACHE: dict[str, Any] | None = None
_FETCHED = False


def get_settings(*, force_refresh: bool = False) -> Optional[dict[str, Any]]:
    """Fetch ``/api/settings`` once per process and cache. Returns
    ``None`` on any failure — callers should treat that as "use
    compiled defaults"."""
    global _CACHE, _FETCHED
    if force_refresh:
        _CACHE = None
        _FETCHED = False
    if _FETCHED:
        return _CACHE
    _CACHE = _fetch()
    _FETCHED = True
    return _CACHE


def clear_cache() -> None:
    """Reset the in-process cache. Useful in tests or after the user
    has explicitly flipped a UI toggle and the next session should
    pick up the new value."""
    global _CACHE, _FETCHED
    _CACHE = None
    _FETCHED = False


def get_placement_mode() -> Optional[str]:
    """Convenience: return ``settings.paper.placementMode`` or
    ``None`` if absent. Default is decided by the caller — the
    settings API returns "auto" or "manual" when configured."""
    s = get_settings()
    if not s:
        return None
    paper = s.get("paper") if isinstance(s, dict) else None
    if not isinstance(paper, dict):
        return None
    mode = paper.get("placementMode")
    if mode in ("auto", "manual"):
        return mode
    return None


def _fetch() -> Optional[dict[str, Any]]:
    """Best-effort GET /api/settings. Silent on failure (DEBUG log)."""
    try:
        import requests
    except ImportError:
        log.debug("requests not installed — api-settings fetch skipped")
        return None
    try:
        from .secrets import get_secret
        base = get_secret("api-base-url")
        token = get_secret("api-token")
        if not base or not token:
            log.debug("api-base-url or api-token missing — api-settings fetch skipped")
            return None
        url = f"{base.rstrip('/')}/api/settings"
        # Short timeout — settings fetch is on the critical path of
        # session startup. Better to fall back to defaults than block.
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                            timeout=4.0)
        if resp.status_code != 200:
            log.debug("api-settings GET returned %s: %s", resp.status_code, resp.text[:200])
            return None
        return resp.json()
    except Exception as e:  # noqa: BLE001 — best-effort
        log.debug("api-settings fetch failed: %s", e)
        return None


__all__ = ["get_settings", "get_placement_mode", "clear_cache"]
