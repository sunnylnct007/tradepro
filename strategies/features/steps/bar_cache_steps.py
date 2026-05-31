"""Step impls for bar_cache.feature.

Synthetic provider + tmpdir cache. No network. No real disk pollution
outside the tmpdir.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from behave import given, then, when

from tradepro_strategies.bar_cache import (
    BarFetchError,
    BarStore,
    SchemaVersionMismatch,
)
from tradepro_strategies.bar_cache.asset_class import register_asset_class
from tradepro_strategies.bar_cache.asset_classes.us_etf import UsEtfPlugin
from tradepro_strategies.bar_cache.providers.base import (
    _clear_registry_for_tests,
    register_provider,
)
from tradepro_strategies.bar_cache.providers.yfinance_provider import (
    YFinanceProvider,
)
from tradepro_strategies.bar_cache.telemetry import NullSink


_START_DEC = datetime(2024, 12, 2, tzinfo=timezone.utc)
_END_DEC = datetime(2024, 12, 31, 23, tzinfo=timezone.utc)


# ─── Background ────────────────────────────────────────────────────


@given("a fresh tmp bar cache base directory")
def step_fresh_tmp_dir(context) -> None:
    if hasattr(context, "_bar_cache_tmpdir") and context._bar_cache_tmpdir:
        shutil.rmtree(context._bar_cache_tmpdir, ignore_errors=True)
    context._bar_cache_tmpdir = tempfile.mkdtemp(prefix="bar_cache_test_")
    context.cache_base = Path(context._bar_cache_tmpdir)
    # Reset registries so a prior scenario's provider doesn't leak.
    _clear_registry_for_tests()


@given("the us_etf asset class plugin is registered")
def step_register_us_etf(context) -> None:
    # The plugin auto-registers at import; just make sure it's present.
    register_asset_class(UsEtfPlugin())


# ─── Synthetic provider helpers ─────────────────────────────────────


def _full_session_bars(d: date) -> pd.DataFrame:
    """390 1-minute bars for a full US session day in UTC. 9:30 ET =
    14:30 UTC for DST; 14:30 every day in this test is fine because
    the bars are synthetic and the manifest only cares about session
    DATES not exact times."""
    idx = pd.date_range(f"{d} 14:30", f"{d} 21:00", freq="1min",
                        tz="UTC", inclusive="left")
    return pd.DataFrame(
        {
            "Open": [100.0] * len(idx),
            "High": [101.0] * len(idx),
            "Low":  [99.0]  * len(idx),
            "Close": [100.5] * len(idx),
            "Volume": [1000] * len(idx),
        },
        index=idx,
    )


def _half_session_bars(d: date) -> pd.DataFrame:
    idx = pd.date_range(f"{d} 14:30", f"{d} 18:00", freq="1min",
                        tz="UTC", inclusive="left")
    return pd.DataFrame(
        {
            "Open": [100.0] * len(idx),
            "High": [101.0] * len(idx),
            "Low":  [99.0]  * len(idx),
            "Close": [100.5] * len(idx),
            "Volume": [1000] * len(idx),
        },
        index=idx,
    )


def _build_dec_2024_frame(dates: list[date]) -> pd.DataFrame:
    half_days = {date(2024, 12, 24)}
    frames = []
    for d in sorted(dates):
        frames.append(
            _half_session_bars(d) if d in half_days else _full_session_bars(d)
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames)


def _make_provider(frame: pd.DataFrame) -> YFinanceProvider:
    def _fetch(symbol, interval, start, end):
        # Filter to the requested window so the partition slicer
        # works correctly.
        if frame.empty:
            return frame
        return frame[(frame.index >= start) & (frame.index < end)]

    return YFinanceProvider(_fetch_fn=_fetch)


# ─── Section 1 — Happy path setups ─────────────────────────────────


@given('a provider "yfinance" returning {n:d} bars for 2024-12-{day:d}')
def step_provider_single_day(context, n: int, day: int) -> None:
    d = date(2024, 12, day)
    frame = _full_session_bars(d) if n >= 390 else _full_session_bars(d).head(n)
    register_provider(_make_provider(frame))


@given('a provider "yfinance" returning a full December 2024 month')
def step_provider_full_month(context) -> None:
    # Every Dec 2024 weekday except 12/25
    cur = date(2024, 12, 2)
    dates = []
    while cur <= date(2024, 12, 31):
        if cur.weekday() < 5 and cur != date(2024, 12, 25):
            dates.append(cur)
        cur += timedelta(days=1)
    register_provider(_make_provider(_build_dec_2024_frame(dates)))


@given('a provider "yfinance" returning bars only for 2024-12-23')
def step_provider_only_dec23(context) -> None:
    register_provider(_make_provider(_full_session_bars(date(2024, 12, 23))))


@given("a recording telemetry sink")
def step_recording_sink(context) -> None:
    context.telemetry = NullSink()


# ─── When clauses ──────────────────────────────────────────────────


def _make_store(context) -> BarStore:
    return BarStore(
        base_dir=context.cache_base,
        telemetry=getattr(context, "telemetry", NullSink()),
    )


@when(
    "I get SPY us_etf 1m bars from "
    "{start_y:d}-{start_m:d}-{start_d:d} to "
    "{end_y:d}-{end_m:d}-{end_d:d} (allow_partial)"
)
def step_get_range_allow_partial(
    context, start_y, start_m, start_d, end_y, end_m, end_d,
):
    store = _make_store(context)
    try:
        context.result = store.get(
            canonical="SPY", asset_class="us_etf", resolution="1m",
            start=datetime(start_y, start_m, start_d, tzinfo=timezone.utc),
            end=datetime(end_y, end_m, end_d, tzinfo=timezone.utc),
            allow_partial=True,
        )
        context.error = None
    except BarFetchError as exc:
        context.result = None
        context.error = exc


@when("I get SPY us_etf 1m bars for full December 2024 (twice)")
def step_get_twice(context):
    store = _make_store(context)
    context.result_1 = store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )
    context.result_2 = store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )


@when("I get SPY us_etf 1m bars for full December 2024 without allow_partial")
def step_get_without_allow_partial(context):
    store = _make_store(context)
    try:
        store.get(
            canonical="SPY", asset_class="us_etf", resolution="1m",
            start=_START_DEC, end=_END_DEC,
        )
        context.error = None
    except BarFetchError as exc:
        context.error = exc


@when("I get SPY us_etf 1m bars for full December 2024 with allow_partial")
def step_get_with_allow_partial(context):
    store = _make_store(context)
    context.result = store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
        allow_partial=True,
    )


@when("I get SPY us_etf 1m bars for full December 2024")
def step_get_basic(context):
    store = _make_store(context)
    context.result = store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )


@given(
    "a previously cached SPY partition 2024-12 with manifest claiming "
    "{n:d} bars"
)
def step_pre_cache_with_manifest(context, n: int):
    # Prime the cache by running a fetch first.
    cur = date(2024, 12, 2)
    dates = []
    while cur <= date(2024, 12, 31):
        if cur.weekday() < 5 and cur != date(2024, 12, 25):
            dates.append(cur)
        cur += timedelta(days=1)
    register_provider(_make_provider(_build_dec_2024_frame(dates)))
    store = _make_store(context)
    store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )


@when("I corrupt the manifest to claim a different schema version")
def step_corrupt_manifest_schema(context):
    mf_path = (
        context.cache_base / "us_etf" / "SPY" / "1m" / "2024-12.manifest.json"
    )
    body = json.loads(mf_path.read_text())
    body["schema_version"] = "us_equity_v_BAD"
    mf_path.write_text(json.dumps(body))


@when("I get SPY us_etf 1m bars for December 2024")
def step_get_after_corruption(context):
    store = _make_store(context)
    try:
        store.get(
            canonical="SPY", asset_class="us_etf", resolution="1m",
            start=_START_DEC, end=_END_DEC,
        )
        context.error = None
    except BarFetchError as exc:
        context.error = exc


@when(
    'I get SPY us_etf bars at resolution "{res}" '
    'from {start_y:d}-{start_m:d}-{start_d:d} to '
    '{end_y:d}-{end_m:d}-{end_d:d}'
)
def step_get_unsupported_resolution(
    context, res, start_y, start_m, start_d, end_y, end_m, end_d,
):
    # Register a recording provider so we can assert "no provider was
    # called" after the rejection.
    context._provider_call_count = 0

    def _spy_fetch(symbol, interval, start, end):
        context._provider_call_count += 1
        return pd.DataFrame()

    register_provider(YFinanceProvider(_fetch_fn=_spy_fetch))

    store = _make_store(context)
    try:
        store.get(
            canonical="SPY", asset_class="us_etf", resolution=res,
            start=datetime(start_y, start_m, start_d, tzinfo=timezone.utc),
            end=datetime(end_y, end_m, end_d, tzinfo=timezone.utc),
        )
        context.error = None
    except BarFetchError as exc:
        context.error = exc


# ─── Then clauses ─────────────────────────────────────────────────


@then("the BarFrame has {n:d} rows")
def step_assert_rows(context, n: int):
    assert context.result is not None
    assert context.result.rows_returned == n, (
        f"rows returned {context.result.rows_returned} != {n}"
    )


@then("the chain shows a cache_miss followed by yfinance_ok")
def step_chain_miss_then_ok(context):
    chain = context.result.provider_chain_tried
    assert "cache_miss" in chain, chain
    assert any("yfinance_ok" in s for s in chain), chain


@then("a parquet file exists for partition {p}")
def step_parquet_exists(context, p):
    pq_path = context.cache_base / "us_etf" / "SPY" / "1m" / f"{p}.parquet"
    assert pq_path.exists(), pq_path


@then("a manifest file exists for partition {p}")
def step_manifest_exists(context, p):
    mf_path = (
        context.cache_base / "us_etf" / "SPY" / "1m" / f"{p}.manifest.json"
    )
    assert mf_path.exists(), mf_path


@then("the manifest declares expected sessions including {iso_date}")
def step_manifest_has_session(context, iso_date):
    mf_path = (
        context.cache_base / "us_etf" / "SPY" / "1m" / "2024-12.manifest.json"
    )
    body = json.loads(mf_path.read_text())
    assert iso_date in body["expected_session_dates"], (
        f"{iso_date} not in {body['expected_session_dates']}"
    )


@then("the second call's chain is exactly cache_hit")
def step_second_chain_cache_hit(context):
    chain = context.result_2.provider_chain_tried
    assert chain == ["cache_hit"], chain


@then('the second call\'s provider_used is "{p}"')
def step_second_provider_used(context, p):
    assert context.result_2.provider_used == p, (
        context.result_2.provider_used
    )


@then("the row counts match between the two calls")
def step_row_counts_match(context):
    assert context.result_1.rows_returned == context.result_2.rows_returned, (
        f"{context.result_1.rows_returned} != "
        f"{context.result_2.rows_returned}"
    )


@then('a BarFetchError is raised with error_class "{ec}"')
def step_error_raised(context, ec):
    assert context.error is not None, "no error raised"
    assert context.error.error_class == ec, (
        f"error_class {context.error.error_class!r} != {ec!r}"
    )


@then('the error\'s actual.missing_sessions includes "{iso_date}"')
def step_error_missing_session(context, iso_date):
    missing = context.error.actual.get("missing_sessions", [])
    assert iso_date in missing, f"{iso_date} not in {missing}"


@then("the BarFrame coverage_complete is False")
def step_coverage_false(context):
    assert context.result is not None
    assert context.result.coverage_complete is False, (
        context.result.coverage_complete
    )


@then("the rows_returned is less than the rows_expected")
def step_rows_less(context):
    assert (
        context.result.rows_returned < context.result.rows_expected
    ), (context.result.rows_returned, context.result.rows_expected)


@then("a SchemaVersionMismatch error is raised")
def step_schema_mismatch(context):
    assert context.error is not None
    assert isinstance(context.error, SchemaVersionMismatch), type(context.error)


@then("no provider was called")
def step_no_provider_call(context):
    assert context._provider_call_count == 0, context._provider_call_count


@then("the recording sink received at least {n:d} events")
@then("the recording sink received at least {n:d} event")
def step_sink_events(context, n: int):
    sink = context.telemetry
    assert len(sink.events) >= n, (
        f"sink has {len(sink.events)} events, expected >= {n}"
    )


@then('the most recent event has result "{r}"')
def step_event_result(context, r):
    sink = context.telemetry
    assert sink.events[-1].result == r, (
        f"event.result {sink.events[-1].result!r} != {r!r}"
    )


@then("the most recent event source_chain is exactly [{items}]")
def step_event_chain_exact(context, items: str):
    sink = context.telemetry
    # Parse "cache_hit" or "a", "b" from feature text.
    parsed = [x.strip().strip('"') for x in items.split(",")]
    actual = sink.events[-1].source_chain
    assert actual == parsed, f"chain {actual} != {parsed}"


@then("the most recent event gaps_detected_count is greater than 0")
def step_event_gaps(context):
    sink = context.telemetry
    assert sink.events[-1].gaps_detected_count > 0, (
        sink.events[-1].gaps_detected_count
    )


@then("no .tmp file exists under the cache directory")
def step_no_tmp(context):
    tmp_files = list(context.cache_base.rglob("*.tmp"))
    assert not tmp_files, f"tmp files survived: {tmp_files}"


@then("the parquet and manifest files exist for partition {p}")
def step_pq_and_mf_exist(context, p):
    pq = context.cache_base / "us_etf" / "SPY" / "1m" / f"{p}.parquet"
    mf = context.cache_base / "us_etf" / "SPY" / "1m" / f"{p}.manifest.json"
    assert pq.exists() and mf.exists(), (pq, mf)


# ─── Section 5: BackendTelemetrySink (Phase B-2) ──────────────────


class _RecordingHttpPoster:
    """Minimal stand-in for requests.post — records URL + json body
    and returns a fake-ok response. Used to verify POST shape without
    hitting the network."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})

        class _OkResponse:
            ok = True
            status_code = 200
            text = ""

        return _OkResponse()


class _FailingHttpPoster:
    """Raises on every call — simulates a backend that's down. The
    sink must swallow the exception and keep the fetch alive."""

    def __call__(self, url, json=None, headers=None, timeout=None):
        raise RuntimeError("synthetic backend outage")


@given("a BackendTelemetrySink with a recording HTTP poster")
def step_recording_backend_sink(context) -> None:
    from tradepro_strategies.bar_cache.telemetry import BackendTelemetrySink
    context._poster = _RecordingHttpPoster()
    context.telemetry = BackendTelemetrySink(
        base_dir=context.cache_base,
        api_base="http://localhost:5252",
        _http_post=context._poster,
    )


@given("a BackendTelemetrySink whose HTTP poster raises an exception")
def step_failing_backend_sink(context) -> None:
    from tradepro_strategies.bar_cache.telemetry import BackendTelemetrySink
    context._poster = _FailingHttpPoster()
    context.telemetry = BackendTelemetrySink(
        base_dir=context.cache_base,
        api_base="http://localhost:5252",
        _http_post=context._poster,
    )


@when("I get SPY us_etf 1m bars for full December 2024 via the backend sink")
def step_get_via_backend_sink(context) -> None:
    # The given that set up context.telemetry has already configured
    # the sink; just run the fetch.
    store = _make_store(context)
    context.result = store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )


@then("the HTTP poster received at least {n:d} request")
@then("the HTTP poster received at least {n:d} requests")
def step_poster_count(context, n: int) -> None:
    assert len(context._poster.requests) >= n, (
        f"poster has {len(context._poster.requests)} requests, expected >= {n}"
    )


@then('the POST URL ends with "{suffix}"')
def step_post_url_ends_with(context, suffix: str) -> None:
    url = context._poster.requests[-1]["url"]
    assert url.endswith(suffix), f"URL {url!r} does not end with {suffix!r}"


@then('the POST body\'s canonical is "{name}"')
def step_post_canonical(context, name: str) -> None:
    body = context._poster.requests[-1]["json"]
    assert body["canonical"] == name, body["canonical"]


@then("the JSONL fallback file exists")
def step_jsonl_exists(context) -> None:
    files = list((context.cache_base / "events").glob("*.jsonl"))
    assert files, "no JSONL files in events/"


@then("the BarFrame coverage_complete is True")
def step_coverage_true(context) -> None:
    assert context.result is not None
    assert context.result.coverage_complete is True, (
        context.result.coverage_complete
    )


# ─── Section 6: DB-driven provider chain (Phase B-3) ──────────────


class _RecordingHttpGetter:
    """Stand-in for ``requests.get`` — records URLs requested and
    returns a fake-ok response with the configured payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requests: list[dict] = []

    def __call__(self, url, headers=None, timeout=None):
        self.requests.append({"url": url, "headers": headers})
        payload = self._payload

        class _OkResponse:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return payload

        return _OkResponse()


class _FailingHttpGetter:
    def __call__(self, url, headers=None, timeout=None):
        raise RuntimeError("synthetic preferences-endpoint outage")


@given('a PreferencesLoader returning [{provider_csv}] for {asset_class} {resolution}')
def step_loader_with_chain(context, provider_csv, asset_class, resolution):
    from tradepro_strategies.bar_cache import PreferencesLoader
    chain = [p.strip().strip('"') for p in provider_csv.split(",")]
    payload = {
        "validProviders": ["yfinance", "ig", "finnhub"],
        "preferences": [
            {
                "asset_class": asset_class,
                "resolution": resolution,
                "provider_chain": chain,
            },
        ],
    }
    context._getter = _RecordingHttpGetter(payload)
    context.loader = PreferencesLoader(
        "http://localhost:5252",
        _http_get=context._getter,
        ttl_seconds=60,
    )


@given('a PreferencesLoader returning no preference for {asset_class} {resolution}')
def step_loader_no_preference(context, asset_class, resolution):
    from tradepro_strategies.bar_cache import PreferencesLoader
    payload = {
        "validProviders": ["yfinance"],
        "preferences": [],  # nothing for the requested tuple
    }
    context._getter = _RecordingHttpGetter(payload)
    context.loader = PreferencesLoader(
        "http://localhost:5252",
        _http_get=context._getter,
        ttl_seconds=60,
    )


@given("a PreferencesLoader whose HTTP getter raises an exception")
def step_loader_failing(context):
    from tradepro_strategies.bar_cache import PreferencesLoader
    context._getter = _FailingHttpGetter()
    context.loader = PreferencesLoader(
        "http://localhost:5252",
        _http_get=context._getter,
        ttl_seconds=60,
    )


@given("a PreferencesLoader with a recording HTTP getter and {ttl:d}s TTL")
def step_loader_recording(context, ttl):
    from tradepro_strategies.bar_cache import PreferencesLoader
    payload = {
        "validProviders": ["yfinance", "ig"],
        "preferences": [
            {
                "asset_class": "us_etf",
                "resolution": "1m",
                "provider_chain": ["yfinance"],
            },
        ],
    }
    context._getter = _RecordingHttpGetter(payload)
    context.loader = PreferencesLoader(
        "http://localhost:5252",
        _http_get=context._getter,
        ttl_seconds=float(ttl),
    )


@when(
    "I get SPY us_etf 1m bars for full December 2024 via the BarStore with that loader"
)
def step_get_with_loader(context):
    store = BarStore(
        base_dir=context.cache_base,
        telemetry=context.telemetry if hasattr(context, "telemetry") else NullSink(),
        preferences_loader=context.loader,
    )
    context.result = store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )


@when("I call chain_for {asset_class} {resolution} twice in a row")
def step_loader_chain_for_twice(context, asset_class, resolution):
    context.loader.chain_for(asset_class, resolution)
    context.loader.chain_for(asset_class, resolution)


@when("I call chain_for {asset_class} {resolution}")
def step_loader_chain_for(context, asset_class, resolution):
    context.loader.chain_for(asset_class, resolution)


@when("I clear the PreferencesLoader cache")
def step_clear_cache(context):
    context.loader.clear_cache()


@then('the chain_source breadcrumb in the source_chain is "{expected}"')
def step_chain_source(context, expected):
    chain = context.result.provider_chain_tried
    found = [s for s in chain if s.startswith("chain_source:")]
    assert found, f"no chain_source breadcrumb in {chain}"
    actual = found[-1].split(":", 1)[1]
    assert actual == expected, f"chain_source={actual!r} != {expected!r}"


@then("the manifest's provider_chain is [{expected_csv}]")
def step_manifest_chain(context, expected_csv):
    import json
    expected = [p.strip().strip('"') for p in expected_csv.split(",")]
    mf = (
        context.cache_base / "us_etf" / "SPY" / "1m" / "2024-12.manifest.json"
    )
    body = json.loads(mf.read_text())
    assert body["provider_chain"] == expected, (
        f"manifest chain {body['provider_chain']} != {expected}"
    )


@then("the HTTP getter received exactly {n:d} request")
@then("the HTTP getter received exactly {n:d} requests")
def step_getter_count(context, n):
    assert len(context._getter.requests) == n, (
        f"getter received {len(context._getter.requests)} requests, "
        f"expected exactly {n}"
    )
