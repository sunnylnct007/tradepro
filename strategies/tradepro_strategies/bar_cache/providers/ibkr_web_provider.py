"""ibkr_web — bar_cache provider that sources OHLCV from IBKR via the CENTRAL
backend endpoint ``/api/integrations/ibkr/price-history`` (which speaks the IBKR
OAuth Web API).

CENTRAL-API design (user rule 2026-07-04, 'central api handling, not scattered'):
the backend ``IBKRClient`` is the SINGLE IBKR handler; this provider is a THIN HTTP
consumer of one endpoint — it does NOT re-implement OAuth, and it avoids the local
IB Gateway (``ibkr_provider``), which hangs on trader+harvester session contention.
Graded GOOD by the quality grader (added to ``_TRUSTED_PROVIDERS``).

FAIL-LOUD (user rule 'don't swallow the error'): a symbol the backend can't price
(unresolved contract / empty history / non-200) raises ProviderNetworkError /
ProviderParseError WITH the backend's reason — never a silent empty frame — so the
harvest records the failure and the cockpit can FLAG the symbol.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from ..errors import ProviderNetworkError, ProviderParseError
from .base import Provider, register_provider

_log = logging.getLogger("tradepro.bar_cache.ibkr_web")

# resolution -> IBKR bar size. NOTE: IBKR uses "1min"/"5min" for MINUTE bars;
# "1m" in IBKR means one MONTH (a *period*, not a bar) — never confuse the two.
# Intraday (1m/5m/15m/30m) added so the Web API is the source for the platform's
# 1-min harvest — off the local Gateway entirely.
_BAR = {"1d": "1d", "1h": "1h", "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min"}
_INTRADAY_RES = {"1m", "5m", "15m", "30m"}


def _ibkr_period(start: datetime, end: datetime, resolution: str = "1d") -> str:
    """IBKR history 'period' window (counts back from now) covering [start, end].
    Intraday resolutions are bounded — IBKR caps 1-min history (~1 month), and a
    huge period on a minute bar is rejected/truncated."""
    days = max(1, (end - start).days)
    if resolution in _INTRADAY_RES:
        if days <= 1:
            return "1d"
        if days <= 7:
            return "1w"
        return "1m"  # ≈ IBKR's practical intraday ceiling (≈30d for 1-min)
    if days <= 30:
        return "1m"
    if days <= 180:
        return "6m"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"


class IBKRWebProvider(Provider):
    """Fetch bars from the central backend IBKR endpoint (Option B / Web API)."""

    name = "ibkr_web"

    # Process-wide budget of auth-cooldown waits (see the 60s-cooldown note in
    # fetch) — class-level so one harvest run can't stall on every symbol.
    _cooldown_waits = 0

    def __init__(
        self,
        base: str | None = None,
        token: str | None = None,
        _get: Callable[[str, dict, int], tuple[int, dict]] | None = None,
    ) -> None:
        # base/token resolved lazily from the secret chain unless injected (tests).
        self._base = base
        self._token = token
        self._get = _get

    def supports_resolution(self, resolution: str) -> bool:
        return resolution in _BAR

    def max_history(self, resolution: str) -> timedelta:
        if resolution not in _BAR:
            return timedelta(0)
        # IBKR caps intraday history (1-min ≈ a month); daily/hourly go back years.
        return timedelta(days=30) if resolution in _INTRADAY_RES else timedelta(days=365 * 5)

    def _resolve_base(self) -> tuple[str, str | None]:
        if self._base:
            return self._base, self._token
        from ...cli import push_to_api
        return push_to_api.load_credentials()

    def fetch(
        self,
        canonical: str,
        asset_class: str,
        resolution: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        if not self.supports_resolution(resolution):
            raise ProviderParseError(
                provider=self.name, canonical=canonical,
                message=f"resolution {resolution!r} not supported by ibkr_web "
                        f"(supported: {sorted(_BAR)})",
            )
        base, token = self._resolve_base()
        base = (base or "").rstrip("/")
        period, bar = _ibkr_period(start, end, resolution), _BAR[resolution]
        url = (f"{base}/api/integrations/ibkr/price-history"
               f"?symbol={canonical}&period={period}&bar={bar}")
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        # 60s (not 30s): the backend retries IBKR's transient history errors
        # in-request (up to 3 attempts, ~10s each worst-case + backoff) — a
        # 30s client timeout would abort mid-retry and turn a recoverable
        # fetch into a yfinance/BRONZE fallthrough.
        _TIMEOUT_S = 60
        try:
            if self._get is not None:
                status, payload = self._get(url, headers, _TIMEOUT_S)
            else:
                import requests
                r = requests.get(url, headers=headers, timeout=_TIMEOUT_S)
                status, payload = r.status_code, (r.json() if r.content else {})
        except Exception as exc:  # noqa: BLE001 — surface as a typed network error
            raise ProviderNetworkError(
                provider=self.name, canonical=canonical,
                message=f"ibkr_web request failed: {exc}") from exc

        payload = payload or {}
        if status != 200:
            reason = payload.get("error") or f"HTTP {status}"
            # AUTH-COOLDOWN WAIT (found live 2026-08-09): one failed IBKR auth
            # puts the backend in a 60s cooldown during which EVERY request
            # fast-fails "backing off" — without this wait, a single cooldown
            # window drains dozens of symbols to yfinance/BRONZE in seconds.
            # Wait it out ONCE per event, bounded to 3 events per process so a
            # persistently-broken auth still fails loud instead of stalling
            # the whole harvest.
            if ("backing off" in str(reason) and "cooldown" in str(reason)
                    and IBKRWebProvider._cooldown_waits < 3):
                IBKRWebProvider._cooldown_waits += 1
                _log.warning(
                    "%s: backend IBKR auth in cooldown — waiting 65s for re-auth "
                    "(wait %d/3 this run) instead of falling through to yfinance",
                    canonical, IBKRWebProvider._cooldown_waits)
                import time as _time
                _time.sleep(65)
                try:
                    if self._get is not None:
                        status, payload = self._get(url, headers, _TIMEOUT_S)
                    else:
                        import requests
                        r = requests.get(url, headers=headers, timeout=_TIMEOUT_S)
                        status, payload = r.status_code, (r.json() if r.content else {})
                except Exception as exc:  # noqa: BLE001
                    raise ProviderNetworkError(
                        provider=self.name, canonical=canonical,
                        message=f"ibkr_web request failed after cooldown wait: {exc}") from exc
                payload = payload or {}
                if status != 200:
                    reason = payload.get("error") or f"HTTP {status}"
                    raise ProviderNetworkError(
                        provider=self.name, canonical=canonical,
                        message=f"ibkr_web (after cooldown wait): {reason}")
            elif is_rate_limited(status, reason):
                # RATE LIMIT — be patient, don't fall through (owner, 15 Aug
                # 2026: "only reason IBKR would fail is rate limiting and we
                # should cater for that, as this can keep running in the
                # background and is not time-pressing").
                #
                # Falling through to yfinance is NOT an equivalent substitute:
                # yfinance serves no deep intraday history (1m ~7 days, 5m
                # ~60), so a rate-limited intraday symbol becomes a permanent
                # hole rather than a slower fill. A background harvest has no
                # deadline, so waiting is strictly better than degrading.
                import time as _time
                waits = _rate_limit_backoff_schedule()
                for attempt, wait_s in enumerate(waits, start=1):
                    _log.warning(
                        "%s: ibkr_web rate-limited (%s) — waiting %ds then retrying "
                        "(%d/%d) rather than degrading to a fallback provider",
                        canonical, reason, wait_s, attempt, len(waits))
                    _time.sleep(wait_s)
                    try:
                        if self._get is not None:
                            status, payload = self._get(url, headers, _TIMEOUT_S)
                        else:
                            import requests
                            r = requests.get(url, headers=headers, timeout=_TIMEOUT_S)
                            status, payload = r.status_code, (r.json() if r.content else {})
                    except Exception as exc:  # noqa: BLE001
                        raise ProviderNetworkError(
                            provider=self.name, canonical=canonical,
                            message=f"ibkr_web request failed during rate-limit backoff: {exc}"
                        ) from exc
                    payload = payload or {}
                    if status == 200:
                        break
                    reason = payload.get("error") or f"HTTP {status}"
                    if not is_rate_limited(status, reason):
                        break
                if status != 200:
                    raise ProviderNetworkError(
                        provider=self.name, canonical=canonical,
                        message=f"ibkr_web: RATE LIMITED after {len(waits)} backoff "
                                f"attempts ({sum(waits)}s total): {reason}")
            else:
                # The endpoint FAILS LOUD (502 + reason); propagate the reason.
                raise ProviderNetworkError(
                    provider=self.name, canonical=canonical, message=f"ibkr_web: {reason}")

        bars = payload.get("bars") or []
        if not bars:
            raise ProviderParseError(
                provider=self.name, canonical=canonical,
                message="ibkr_web returned no bars (backend reported success but empty)")

        df = pd.DataFrame(bars)
        # t = epoch MILLISECONDS UTC → tz-aware index; o/h/l/c/v → lowercase schema.
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = (df.set_index("timestamp")
                .rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
                [["open", "high", "low", "close", "volume"]]
                .sort_index())
        # bar_cache schema REQUIRES adj_factor + source (column_order = open/high/low/
        # close/volume/adj_factor/source). Without BOTH, BarStore rejects the frame
        # (ibkr_web_parse) and silently falls through to yfinance = BRONZE. IBKR
        # history is split/dividend-adjusted "Last" → adj_factor = 1.0 (like the
        # Gateway). source drives the quality grade (ibkr_web ∈ _TRUSTED_PROVIDERS → GOOD).
        df["adj_factor"] = 1.0
        df["source"] = "ibkr_web"

        s = pd.Timestamp(start); e = pd.Timestamp(end)
        if s.tz is None:
            s = s.tz_localize("UTC")
        if e.tz is None:
            e = e.tz_localize("UTC")
        df = df[(df.index >= s) & (df.index <= e)]
        if df.empty:
            raise ProviderParseError(
                provider=self.name, canonical=canonical,
                message=f"ibkr_web: {len(bars)} bar(s) returned but none within "
                        f"[{s.date()}, {e.date()}]")

        meta = {"provider": self.name, "rows": int(len(df)),
                "conid": payload.get("conid"), "period": period, "bar": bar}
        return df, meta


register_provider(IBKRWebProvider())


# ── Rate-limit handling ───────────────────────────────────────────────────
# IBKR's pacing rejections are the single most likely reason a symbol falls
# back to yfinance in a 179-symbol sequential harvest. They are also the most
# recoverable: the job runs in the background with no deadline, so waiting
# beats degrading — especially for intraday, where yfinance simply has no deep
# history to fall back TO.
_RATE_LIMIT_MARKERS = (
    "rate limit", "ratelimit", "too many requests", "pacing",
    "max rate", "exceeded", "429",
)


def is_rate_limited(status: int | None, reason: object) -> bool:
    """True when a response looks like IBKR pacing/rate limiting rather than a
    genuine data or auth failure. Pure — unit-tested."""
    if status == 429:
        return True
    text = str(reason or "").lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def _rate_limit_backoff_schedule() -> list[int]:
    """Waits (seconds) between retries. Generous by design: a background
    harvest has no deadline, and a filled IBKR bar is worth minutes of waiting.
    Override with TRADEPRO_IBKR_RATELIMIT_BACKOFF (comma-separated seconds)."""
    import os
    raw = os.environ.get("TRADEPRO_IBKR_RATELIMIT_BACKOFF", "").strip()
    if raw:
        try:
            return [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            pass
    return [15, 45, 120, 300]
