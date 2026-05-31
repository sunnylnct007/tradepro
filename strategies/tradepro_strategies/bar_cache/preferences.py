"""PreferencesLoader — read the provider chain from the
data_source_preferences table.

Phase A built the editable provider preferences UI + the .NET
endpoint that returns the table contents. Phase B-3 (this module)
closes the loop: the Python BarStore consults this loader when
deciding which provider to call for each (asset_class, resolution)
tuple, so a flip in the UI takes effect on the next fetch (within
the TTL window).

Cached for a short TTL so we don't pay an HTTP roundtrip on every
BarStore.get() call inside a tight sweep loop. The default 60s means
an operator edit propagates "within the next minute" — good enough
for trade support, fast enough that nobody waits for a stale chain
to drain.

Best-effort. The loader is non-fatal: a 404 / network error / parse
error returns ``None`` from ``chain_for()`` and the caller falls
back to the BarStore's hardcoded default chain. The trustworthy-data
contract (partial reads banned) is upheld by the BarStore itself —
the loader's job is just provider routing.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional


_log = logging.getLogger("tradepro.bar_cache.preferences")


class PreferencesLoader:
    """Fetch + cache the provider chain per (asset_class, resolution).

    Public surface:
        chain_for(asset_class, resolution) -> list[str] | None
        clear_cache() -> None
        valid_providers() -> list[str] | None

    Injectable ``_http_get`` callable for tests (signature must match
    ``requests.get(url, *, headers, timeout) -> Response-like``).
    Without it, ``requests`` is imported lazily in the production
    path."""

    def __init__(
        self,
        api_base: str,
        *,
        auth_token: Optional[str] = None,
        ttl_seconds: float = 60.0,
        timeout_seconds: float = 5.0,
        _http_get: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._auth_token = auth_token
        self._ttl = float(ttl_seconds)
        self._timeout = float(timeout_seconds)
        self._http_get = _http_get

        # Single snapshot of the full table is cheaper than per-tuple
        # lookups — the API returns every row anyway. The cache is
        # ``(fetched_at, payload)``; ``payload`` is the JSON body.
        self._snapshot: Optional[dict[str, Any]] = None
        self._snapshot_at: float = 0.0
        # Sentinel for fetch failure — we hold the failure briefly so
        # a flapping backend doesn't cause a thundering herd. Shorter
        # than the success TTL so recovery is fast.
        self._last_failure_at: float = 0.0
        self._failure_cooldown_seconds: float = 5.0

    # ── Public ──────────────────────────────────────────────────────

    def chain_for(
        self, asset_class: str, resolution: str,
    ) -> Optional[list[str]]:
        """Resolved provider chain for the tuple, or ``None`` if no
        preference is configured (caller uses its default).

        Cached: a snapshot of the full preferences table is held for
        ``ttl_seconds``. Stale snapshots are silently refreshed."""
        snapshot = self._get_snapshot_or_none()
        if snapshot is None:
            return None
        for row in snapshot.get("preferences", []):
            if (
                row.get("asset_class") == asset_class
                and row.get("resolution") == resolution
            ):
                chain = row.get("provider_chain")
                if isinstance(chain, list) and chain:
                    return [str(p) for p in chain]
        # Row not present in the table → no opinion. Caller uses default.
        return None

    def valid_providers(self) -> Optional[list[str]]:
        """The allow-list the backend reports as valid providers.
        Useful for the CLI to validate a manual override before
        sending a request. Returns ``None`` if the snapshot couldn't
        be fetched."""
        snapshot = self._get_snapshot_or_none()
        if snapshot is None:
            return None
        return [str(p) for p in snapshot.get("validProviders", [])]

    def clear_cache(self) -> None:
        """Force a refresh on the next ``chain_for`` call. The CLI's
        ``--force-refresh`` flag should call this so operators can
        re-pull preferences without restarting the process."""
        self._snapshot = None
        self._snapshot_at = 0.0
        self._last_failure_at = 0.0

    # ── Internals ──────────────────────────────────────────────────

    def _get_snapshot_or_none(self) -> Optional[dict[str, Any]]:
        now = time.time()
        # Hot path — within TTL, serve from cache.
        if self._snapshot is not None and (now - self._snapshot_at) < self._ttl:
            return self._snapshot
        # Failure cooldown — if we hit a failure recently, don't
        # hammer the backend until the cooldown expires.
        if (
            self._snapshot is None
            and self._last_failure_at > 0
            and (now - self._last_failure_at) < self._failure_cooldown_seconds
        ):
            return None
        try:
            payload = self._fetch_snapshot()
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "preferences fetch failed (using default chain): %s",
                exc,
            )
            self._last_failure_at = now
            # Keep any prior snapshot rather than nuking it — a brief
            # outage shouldn't force every BarStore to default. Only
            # nuke if we had nothing.
            if self._snapshot is None:
                return None
            # Stale-while-error: return the old snapshot.
            _log.info("serving stale preferences snapshot during outage")
            return self._snapshot
        self._snapshot = payload
        self._snapshot_at = now
        self._last_failure_at = 0.0
        return self._snapshot

    def _fetch_snapshot(self) -> dict[str, Any]:
        url = f"{self._api_base}/api/admin/data-trust/preferences"
        headers = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._http_get is not None:
            resp = self._http_get(url, headers=headers, timeout=self._timeout)
        else:
            import requests
            resp = requests.get(url, headers=headers, timeout=self._timeout)
        if not getattr(resp, "ok", False):
            raise RuntimeError(
                f"preferences endpoint returned "
                f"{getattr(resp, 'status_code', '?')}: "
                f"{getattr(resp, 'text', '')[:200]}"
            )
        return resp.json()
