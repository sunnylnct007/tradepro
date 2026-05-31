"""Steps for data_worker.feature — Phase C-Validate.

Exercises the modular data_ops pipeline (registry + dispatch + handler
+ storage) end-to-end against a tmpdir bar cache. No network, no
launchd, no real disk outside the tmpdir.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from behave import given, then, when

from tradepro_strategies.bar_cache import BarStore
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin  # noqa: F401
from tradepro_strategies.bar_cache.providers.base import (
    _clear_registry_for_tests,
    register_provider,
)
from tradepro_strategies.bar_cache.providers.yfinance_provider import (
    YFinanceProvider,
)
from tradepro_strategies.bar_cache.telemetry import NullSink
from tradepro_strategies.data_ops import (
    DataOpRequest,
    LocalBarCacheStorage,
    dispatch,
    list_kinds,
)


_START_DEC = datetime(2024, 12, 2, tzinfo=timezone.utc)
_END_DEC = datetime(2024, 12, 31, 23, tzinfo=timezone.utc)


def _full_session_bars(d: date) -> pd.DataFrame:
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


def _build_dec_2024_frame(dates):
    half_days = {date(2024, 12, 24)}
    frames = []
    for d in sorted(dates):
        frames.append(
            _half_session_bars(d) if d in half_days else _full_session_bars(d)
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames)


# ─── Given ───────────────────────────────────────────────────────


def _new_tmp_cache(context) -> Path:
    context._dw_tmpdir = tempfile.mkdtemp(prefix="data_worker_test_")
    context.cache_base = Path(context._dw_tmpdir)
    context.add_cleanup(
        lambda: shutil.rmtree(context._dw_tmpdir, ignore_errors=True),
    )
    return context.cache_base


def _populate_single_day(base: Path) -> None:
    _clear_registry_for_tests()
    df = _full_session_bars(date(2024, 12, 23))

    def fake_fetch(symbol, interval, start, end):
        return df[(df.index >= start) & (df.index < end)]

    register_provider(YFinanceProvider(_fetch_fn=fake_fetch))
    store = BarStore(base_dir=base, telemetry=NullSink())
    store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=datetime(2024, 12, 23, tzinfo=timezone.utc),
        end=datetime(2024, 12, 24, tzinfo=timezone.utc),
        allow_partial=True,
    )


def _populate_full_month(base: Path) -> None:
    _clear_registry_for_tests()
    cur = date(2024, 12, 2)
    dates = []
    while cur <= date(2024, 12, 31):
        if cur.weekday() < 5 and cur != date(2024, 12, 25):
            dates.append(cur)
        cur += timedelta(days=1)
    full_df = _build_dec_2024_frame(dates)

    def fake_fetch(symbol, interval, start, end):
        return full_df[(full_df.index >= start) & (full_df.index < end)]

    register_provider(YFinanceProvider(_fetch_fn=fake_fetch))
    store = BarStore(base_dir=base, telemetry=NullSink())
    store.get(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        start=_START_DEC, end=_END_DEC,
    )


@given("a tmp bar cache populated with a single-day SPY partition")
def step_single_day_cache(context):
    base = _new_tmp_cache(context)
    _populate_single_day(base)


@given("a tmp bar cache populated with a full December 2024 SPY partition")
def step_full_month_cache(context):
    base = _new_tmp_cache(context)
    _populate_full_month(base)


@given("a tmp bar cache with no SPY directory")
def step_empty_cache(context):
    _new_tmp_cache(context)


# ─── When ────────────────────────────────────────────────────────


def _ensure_storage(context):
    """Lazy storage construction for scenarios that don't populate
    the cache (e.g. the empty-params test). Reuses the tmpdir if
    a Given already created one."""
    if not hasattr(context, "cache_base"):
        _new_tmp_cache(context)
    return LocalBarCacheStorage(context.cache_base)


@when("I dispatch a data_validate request for SPY us_etf")
def step_dispatch_validate(context):
    storage = _ensure_storage(context)
    context.dop_result = dispatch(
        DataOpRequest(
            request_id="r-test",
            kind="data_validate",
            params={"canonical": "SPY", "asset_class": "us_etf"},
        ),
        storage,
    )


@when("I dispatch a data_validate request with empty params")
def step_dispatch_validate_empty(context):
    storage = _ensure_storage(context)
    context.dop_result = dispatch(
        DataOpRequest(request_id="r-empty", kind="data_validate", params={}),
        storage,
    )


@when('I dispatch a data_op of kind "{kind}"')
def step_dispatch_unknown(context, kind: str):
    storage = _ensure_storage(context)
    context.dop_result = dispatch(
        DataOpRequest(request_id="r-bogus", kind=kind, params={}),
        storage,
    )


@when("I list registered data_op kinds")
def step_list_kinds(context):
    context.dop_kinds = list_kinds()


# ─── Then ────────────────────────────────────────────────────────


@then("the data op result is ok")
def step_result_ok(context):
    assert context.dop_result.ok is True, (
        f"ok=False; summary={context.dop_result.summary!r} "
        f"error={context.dop_result.error!r}"
    )


@then("the data op result is not ok")
def step_result_not_ok(context):
    assert context.dop_result.ok is False, (
        f"ok=True; summary={context.dop_result.summary!r}"
    )


@then('the data op result detail canonical is "{val}"')
def step_detail_canonical(context, val):
    assert context.dop_result.detail["canonical"] == val


@then('the data op result detail asset_class is "{val}"')
def step_detail_asset_class(context, val):
    assert context.dop_result.detail["asset_class"] == val


@then("the data op result detail exists is True")
def step_detail_exists_true(context):
    assert context.dop_result.detail["exists"] is True


@then("the data op result detail exists is False")
def step_detail_exists_false(context):
    assert context.dop_result.detail["exists"] is False


@then('the data op result detail resolutions include "{res}"')
def step_resolutions_include(context, res: str):
    assert res in context.dop_result.detail["resolutions"], (
        list(context.dop_result.detail["resolutions"].keys())
    )


@then("the {res} resolution incomplete_count is greater than 0")
def step_incomplete_gt_zero(context, res: str):
    n = context.dop_result.detail["resolutions"][res]["incomplete_count"]
    assert n > 0, f"incomplete_count = {n}"


@then("the {res} resolution complete_count is {n:d}")
def step_complete_count(context, res: str, n: int):
    actual = context.dop_result.detail["resolutions"][res]["complete_count"]
    assert actual == n, f"complete_count {actual} != {n}"


@then("the {res} resolution incomplete_count is {n:d}")
def step_incomplete_count_exact(context, res: str, n: int):
    actual = context.dop_result.detail["resolutions"][res]["incomplete_count"]
    assert actual == n, f"incomplete_count {actual} != {n}"


@then('the data op result summary mentions "{phrase}"')
def step_summary_mentions(context, phrase: str):
    assert phrase in context.dop_result.summary, (
        f"summary {context.dop_result.summary!r} does not mention {phrase!r}"
    )


@then('the data op result error mentions "{phrase}"')
def step_error_mentions(context, phrase: str):
    err = context.dop_result.error or ""
    assert phrase in err, (
        f"error {err!r} does not mention {phrase!r}"
    )


@then('the registered kinds include "{kind}"')
def step_kinds_include(context, kind: str):
    assert kind in context.dop_kinds, (
        f"{kind!r} not in {context.dop_kinds}"
    )


@then('the storage describe reports backend "{backend}"')
def step_storage_describe(context, backend: str):
    storage = _ensure_storage(context)
    assert storage.describe().get("backend") == backend, storage.describe()


# ─── Phase C-Backfill scenarios ──────────────────────────────────


@given("a synthetic yfinance provider returning a full December 2024 month")
def step_synthetic_yfinance(context):
    """Register a YFinanceProvider with an injected fetcher so the
    BackfillHandler's BarStore call lands on synthetic data. Same
    pattern the bar_cache.feature §1 happy-path scenarios use."""
    _clear_registry_for_tests()

    cur = date(2024, 12, 2)
    dates = []
    while cur <= date(2024, 12, 31):
        if cur.weekday() < 5 and cur != date(2024, 12, 25):
            dates.append(cur)
        cur += timedelta(days=1)
    full_df = _build_dec_2024_frame(dates)

    def fake_fetch(symbol, interval, start, end):
        return full_df[(full_df.index >= start) & (full_df.index < end)]

    register_provider(YFinanceProvider(_fetch_fn=fake_fetch))


@when(
    "I dispatch a data_backfill request for SPY us_etf 1m "
    "{from_y:d}-{from_m:d}-{from_d:d} to "
    "{to_y:d}-{to_m:d}-{to_d:d}"
)
def step_dispatch_backfill(
    context, from_y, from_m, from_d, to_y, to_m, to_d,
):
    storage = _ensure_storage(context)
    context.dop_result = dispatch(
        DataOpRequest(
            request_id="r-backfill",
            kind="data_backfill",
            params={
                "canonical": "SPY",
                "asset_class": "us_etf",
                "resolution": "1m",
                "from": f"{from_y:04d}-{from_m:02d}-{from_d:02d}",
                "to":   f"{to_y:04d}-{to_m:02d}-{to_d:02d}",
            },
        ),
        storage,
    )


@when("I dispatch a data_backfill request with empty params")
def step_dispatch_backfill_empty(context):
    storage = _ensure_storage(context)
    context.dop_result = dispatch(
        DataOpRequest(
            request_id="r-empty-backfill",
            kind="data_backfill",
            params={},
        ),
        storage,
    )


@when(
    'I dispatch a data_backfill request for SPY us_etf 1m with from "{bad_date}"'
)
def step_dispatch_backfill_bad_date(context, bad_date: str):
    storage = _ensure_storage(context)
    context.dop_result = dispatch(
        DataOpRequest(
            request_id="r-bad-date",
            kind="data_backfill",
            params={
                "canonical": "SPY",
                "asset_class": "us_etf",
                "resolution": "1m",
                "from": bad_date,
                "to": "2024-12-31",
            },
        ),
        storage,
    )


@then('the data op result summary contains "{phrase}"')
def step_summary_contains(context, phrase: str):
    assert phrase in context.dop_result.summary, (
        f"summary {context.dop_result.summary!r} does not contain {phrase!r}"
    )


@then("the data op result detail partitions_before is {n:d}")
def step_partitions_before(context, n: int):
    actual = context.dop_result.detail.get("partitions_before")
    assert actual == n, f"partitions_before {actual} != {n}"


@then("the data op result detail partitions_after is {n:d}")
def step_partitions_after(context, n: int):
    actual = context.dop_result.detail.get("partitions_after")
    assert actual == n, f"partitions_after {actual} != {n}"


@then("the data op result detail partitions_added is {n:d}")
def step_partitions_added(context, n: int):
    actual = context.dop_result.detail.get("partitions_added")
    assert actual == n, f"partitions_added {actual} != {n}"


@then('the data op result detail missing includes "{field}"')
def step_detail_missing_includes(context, field: str):
    missing = context.dop_result.detail.get("missing") or []
    assert field in missing, f"{field!r} not in missing list {missing}"
