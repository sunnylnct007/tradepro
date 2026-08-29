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
from datetime import datetime, timedelta, timezone
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
# Largest intraday span IBKR will serve in ONE request. A whole-month ask
# (period="1m") returns 503 Service Unavailable; a 5-day ask returns cleanly.
# 7 days keeps us inside that and matches the "1w" period the mapping already
# emits, so the request is one IBKR understands well.
# IBKR historical bars report volume in lots of 100 shares for US listings.
_IBKR_VOLUME_LOT_SIZE = 100

_INTRADAY_CHUNK_DAYS = 7
# IBKR caps a history response at 1000 BARS regardless of the period asked
# for (measured 22 Aug 2026: period=1m and period=3m both returned exactly
# 1000). A fixed 7-day chunk is therefore wrong in BOTH directions:
#   1m  — 7 days is ~2,730 bars, so only the most recent 1000 came back and
#         roughly 4.5 days of every chunk were SILENTLY LOST.
#   5m  — 7 days is ~429 bars, under half the cap, so we paid 2.3x the
#         requests a backfill needed.
# Sized here from bars-per-session with headroom under the cap.
_CHUNK_DAYS_BY_RES: dict[str, int] = {
    "1m": 3,     # ~390/session → 2.5 sessions fits
    "5m": 14,    # ~78/session  → 10 sessions ≈ 780 bars
    "15m": 45,   # ~26/session
    "30m": 90,   # ~13/session
    # 1d added 28 Aug 2026. Daily was NOT chunked, and the 1000-bar response
    # cap therefore bound every daily request: asking for 5, 10, 15, 20 or 25
    # years of AAPL all returned the SAME 998 bars back to 2022-09-06. The
    # store's history was capped at ~4 years by a limit nobody had connected
    # to daily bars, and `max_history`'s unmeasured `365*5` fallback got the
    # blame. ~250 sessions/year, so 1000 calendar days ≈ 690 sessions — a
    # comfortable margin under the cap.
    #
    # This does not touch the nightly harvest, which asks for ~10 days and so
    # never trips the chunking threshold. It only engages on deep backfills.
    "1d": 1000,
}


def _ibkr_period(start: datetime, end: datetime, resolution: str = "1d",
                 now: datetime | None = None) -> str:
    """IBKR history 'period' window (counts back from now) covering [start, end].
    Intraday resolutions are bounded — IBKR caps 1-min history (~1 month), and a
    huge period on a minute bar is rejected/truncated.

    MEASURED FROM NOW, NOT THE SPAN (fixed 16 Aug 2026). IBKR's `period`
    counts BACKWARDS from the present, so it must cover now→start. This used
    to use `(end - start)`, which meant a request for an OLD window asked for a
    period of that window's WIDTH and therefore fetched the most RECENT bars
    instead — which the caller then filtered away, producing the
    "N bar(s) returned but none within [...]" failures. Masked for daily
    (those fetches usually run to now, so span ≈ distance back), fatal for any
    historical window.
    """
    # Match `start`'s awareness — callers pass both naive and tz-aware datetimes,
    # and mixing them raises. Defaulting to UTC-aware broke the naive callers.
    if now is None:
        now = datetime.now(start.tzinfo) if start.tzinfo is not None else datetime.now()
    elif (now.tzinfo is None) != (start.tzinfo is None):
        now = (now.replace(tzinfo=timezone.utc) if now.tzinfo is None
               else now.astimezone(timezone.utc).replace(tzinfo=None))
    # Cover the whole of [start, end] as seen from now; +1 so a same-day
    # window still asks for at least a day.
    days = max(1, (now - start).days + 1)
    if resolution in _INTRADAY_RES:
        if days <= 1:
            return "1d"
        if days <= 7:
            return "1w"
        # 2w/3w matter now that period is measured from NOW: a chunk covering
        # 10 days ago needs a window reaching back that far, and rounding it
        # up to "1m" is what draws the 503.
        if days <= 14:
            return "2w"
        if days <= 21:
            return "3w"
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

    def _fetch_chunked(
        self, canonical: str, asset_class: str, resolution: str,
        start: datetime, end: datetime,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Walk [start, end) in <=_INTRADAY_CHUNK_DAYS slices and concatenate.

        Partial tolerance is deliberate: IBKR caps intraday history at ~30 days,
        so the OLDEST slices of a monthly partition legitimately return nothing.
        Failing the whole fetch because the first week is out of reach would
        hand the partition straight back to yfinance — the exact outcome this
        exists to prevent. We raise only if EVERY slice fails.
        """
        frames: list[pd.DataFrame] = []
        meta: dict[str, Any] = {}
        errors: list[str] = []
        cursor = start
        step = timedelta(days=_CHUNK_DAYS_BY_RES.get(resolution, _INTRADAY_CHUNK_DAYS))
        while cursor < end:
            chunk_end = min(cursor + step, end)
            try:
                df, m = self.fetch(canonical, asset_class, resolution, cursor, chunk_end)
                if df is not None and not df.empty:
                    frames.append(df)
                    meta = m or meta
            except Exception as exc:  # noqa: BLE001 — a dead slice is expected
                errors.append(f"{cursor.date()}→{chunk_end.date()}: {str(exc)[:80]}")
            cursor = chunk_end

        if not frames:
            raise ProviderNetworkError(
                provider=self.name, canonical=canonical,
                message=(f"ibkr_web: all {len(errors)} intraday slice(s) failed for "
                         f"{canonical} [{start.date()}→{end.date()}]; "
                         f"first: {errors[0] if errors else 'no slices'}"),
            )
        out = pd.concat(frames)
        out = out[~out.index.duplicated(keep="first")].sort_index()
        meta = dict(meta)
        meta["chunks"] = len(frames)
        meta["chunks_failed"] = len(errors)
        return out, meta

    name = "ibkr_web"

    # Process-wide budget of auth-cooldown waits (see the 60s-cooldown note in
    # fetch) — class-level so one harvest run can't stall on every symbol.
    #
    # CONFIGURABLE since 29 Aug 2026. The hardcoded 3 is right for a nightly
    # harvest, which makes ONE request per symbol: three transient cooldowns in
    # a run means auth is genuinely broken and failing loud is correct.
    #
    # It is badly wrong for a BACKFILL. A chunked 3-year 5m fetch makes hundreds
    # of requests, so the budget drains on the first blip and every request
    # afterwards fast-fails and silently serves cache. Measured that day: a
    # SPY/QQQ backfill returned "4,978 / 58,518 bars ... source=cache" and
    # reported 0 failed. IBKR was fine — a direct fetch of the same windows
    # moments later returned every bar. Owner, on seeing it: "i am not using
    # ibkr currently and it shd connct and download", "we just have to retry
    # with force".
    _cooldown_waits = 0

    @staticmethod
    def _cooldown_budget() -> int:
        import os as _os
        try:
            return max(0, int(_os.environ.get("TRADEPRO_IBKR_COOLDOWN_WAITS", "3")))
        except ValueError:
            return 3

    @classmethod
    def reset_cooldown_budget(cls) -> None:
        """Long-running callers (a backfill loop) call this between symbols so
        one symbol's blips do not condemn the rest of the run."""
        cls._cooldown_waits = 0

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

    # MEASURED against the live account 22 Aug 2026 (SPY, 4-day probe windows
    # at 1/6/12/24/36 months back), not assumed. The old code returned 30 days
    # for EVERY intraday resolution, so BarStore clipped any older window to
    # zero width and skipped this provider as "out_of_range" — the provider was
    # never even called. That is why deep intraday never accumulated: median 14
    # sessions of 5m across the tradeable universe and not one symbol with a
    # year, while IBKR was willing to serve 5m from three years back all along.
    #
    # Erring LONG is the safe direction: an over-long limit costs one failed
    # request that the chain handles, while an over-short one makes the data
    # silently unreachable — which is the bug being fixed here.
    _MAX_HISTORY_DAYS: dict[str, int] = {
        "1m": 200,     # worked at 6 months, failed at 12
        "5m": 1095,    # worked at 12, 24 AND 36 months
        "15m": 1095,
        "30m": 1095,
        "1h": 730,     # worked at 24 months, failed at 36
    }

    def max_history(self, resolution: str) -> timedelta:
        if resolution not in _BAR:
            return timedelta(0)
        if resolution in self._MAX_HISTORY_DAYS:
            return timedelta(days=self._MAX_HISTORY_DAYS[resolution])
        # Daily. The old `365*5` here was an UNMEASURED fallback — every other
        # entry in _MAX_HISTORY_DAYS carries its evidence ("worked at 24
        # months, failed at 36") while this one carried none, and the store
        # clips the fetch window to it (store.py `max_hist`). It was also
        # blamed for a cap it did not cause: the real limiter was the 1000-bar
        # response ceiling, because daily requests were never chunked.
        #
        # With chunking in place the depth is IBKR's, not ours. 30 years is a
        # ceiling rather than a claim — the walk simply stops returning bars at
        # a symbol's inception, and `_fetch_chunked` tolerates empty slices by
        # design. It must not be tightened back to a guess: doing so silently
        # truncates every backfill, which is how the store ended up with a
        # 2021-2022 first-bar cluster across most of the universe.
        return timedelta(days=365 * 30)

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
        # CHUNK INTRADAY (16 Aug 2026) — the root cause of a yfinance-heavy
        # intraday store. BarStore always fetches a WHOLE MONTHLY PARTITION, so
        # every intraday call asked IBKR for period="1m" (one month) of 1/5-min
        # bars and got back:
        #     {"error":"Service Unavailable","statusCode":503}
        # The chain then fell to the retired local Gateway (connection refused)
        # and finally to yfinance, which wrote the bars — permanently, because a
        # cache hit is never re-sourced. That is how 1m ended up 1% IBKR /
        # 99% yfinance while the SAME provider served 279 bars on demand for a
        # 5-day window seconds later.
        #
        # Same remedy the yfinance provider already uses for Yahoo's own
        # per-request cap (`_call_yfinance_chunked`): ask for short spans and
        # stitch. Not a workaround — a request-size limit is a property of the
        # API and belongs in the adapter.
        # Chunk whenever the ASK exceeds what one response can carry — that is
        # a property of the request-size limit, not of intraday. Daily was
        # excluded here, so a deep backfill made ONE request and silently got
        # the most recent 1000 bars (28 Aug 2026: 5y/10y/25y asks for AAPL all
        # returned the same 998 bars from 2022-09-06).
        if resolution in _CHUNK_DAYS_BY_RES or resolution in _INTRADAY_RES:
            span = (end - start)
            if span > timedelta(days=_CHUNK_DAYS_BY_RES.get(resolution, _INTRADAY_CHUNK_DAYS)):
                return self._fetch_chunked(canonical, asset_class, resolution, start, end)
        base, token = self._resolve_base()
        base = (base or "").rstrip("/")
        period, bar = _ibkr_period(start, end, resolution), _BAR[resolution]
        url = (f"{base}/api/integrations/ibkr/price-history"
               f"?symbol={canonical}&period={period}&bar={bar}")
        # ANCHOR HISTORICAL WINDOWS (22 Aug 2026). IBKR measures `period`
        # backward from NOW unless a startTime is given, so a request for an
        # older window returned the most RECENT bars and this provider
        # correctly rejected them ("N bars returned but none within [...]").
        # That is why intraday history never accumulated — median 14 sessions
        # of 5m across the tradeable universe and not one symbol with a year.
        # Anchor on the window's END so IBKR walks backward into the window.
        # Only for windows that are meaningfully in the past: a live/current
        # window must keep measuring from now, or we would pin it to a stale
        # anchor and miss today's bars.
        # Naive datetimes reach this provider (callers are inconsistent), so
        # normalise before comparing — a tz mix raises TypeError.
        _end_utc = end if end.tzinfo is not None else end.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - _end_utc) > timedelta(days=1):
            url += f"&startTime={_end_utc.strftime('%Y%m%d-%H:%M:%S')}"
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
            _budget = IBKRWebProvider._cooldown_budget()
            _in_cooldown = ("backing off" in str(reason) and "cooldown" in str(reason))
            if _in_cooldown and IBKRWebProvider._cooldown_waits >= _budget:
                # SAY SO. Exhausting the budget used to be silent: every later
                # request fast-failed, the store served cache, and the run
                # reported "0 failed" on a fraction of the bars.
                _log.error(
                    "%s: IBKR auth cooldown and the wait budget is EXHAUSTED "
                    "(%d/%d used). Every remaining request will fast-fail and "
                    "serve CACHE — the run will look successful on partial data. "
                    "Raise TRADEPRO_IBKR_COOLDOWN_WAITS for a backfill.",
                    canonical, IBKRWebProvider._cooldown_waits, _budget)
            if _in_cooldown and IBKRWebProvider._cooldown_waits < _budget:
                IBKRWebProvider._cooldown_waits += 1
                _log.warning(
                    "%s: backend IBKR auth in cooldown — waiting 65s for re-auth "
                    "(wait %d/%d this run) instead of falling through to yfinance",
                    canonical, IBKRWebProvider._cooldown_waits, _budget)
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
                # NO lot conversion here. THE BACKEND ALREADY DID IT.
                #
                # IBKR reports historical volume in 100-share lots, and on
                # 23 Aug 2026 I applied the ×100 in two places for one pipeline:
                # `IBKRResponseParser.ParseHistory` in the C# API, and here. But
                # this provider does not talk to IBKR — it fetches
                # `/api/integrations/ibkr/price-history` from our own backend,
                # which returns bars that have ALREADY been through ParseHistory.
                # So every row written by this path came out ×100 too high.
                #
                # Measured against the endpoint this provider reads, TXN 1d:
                #     API  /price-history   2,212,121        (correct, shares)
                #     parquet store       221,212,100        (×100)
                # and SPY's stored August bars read 5.9 BILLION shares a day
                # against a real ~59 million — a tenth of all US equity volume
                # in a single ETF.
                #
                # The failure mode is the one this codebase produces most: the
                # same correction applied at two layers of one pipeline, each
                # site individually defensible. The lot conversion belongs at
                # the boundary where raw IBKR JSON is parsed — ParseHistory —
                # and nowhere else downstream of it.
                #
                # Existing rows are NOT repaired here: see DATA_CHANGE_LOG.
                # Volume feeds the universe's dollar-turnover floor, and the
                # universe is frozen for the forward test.
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
