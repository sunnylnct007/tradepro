"""The bar cache has ONE reader: BarStore. Not the filesystem.

WHY THIS GUARD EXISTS. The bar cache is a SHARED store — local disk is a
read-through cache in front of an S3 bucket, and `BarStore._ensure_local`
downloads a partition on a local miss. Anything that reaches past the store
with `glob.glob(".../1d/*.parquet")` sees only what happens to be on THAT disk:

  * it silently under-reads on any host whose local cache is incomplete,
  * it under-reads here too, the moment a local partition is pruned or a
    harvest writes from somewhere else,
  * and it invents a machine-local dependency that the S3 mirror exists to
    remove. On 29 Aug a new screener was written with a raw glob and the
    commit message asserted it "must run on the Mac because it reads the local
    bar store". That was only true BECAUSE of the glob.

The owner's instruction, the same day: *"we moved the data harvesting to s3 ...
ensure we not messing up the data harvesting and usage again"* and *"i would
like to delete any flaky code going forward so other agent dont fall in same
trap where we stra having 2 source of truth"*.

PROVEN, not assumed: deleting `us_etf/KO/1d/2026-07.parquet` and re-running the
migrated screener read the bars back and restored the file from S3. The same
deletion against a globbing screen loses that month silently.

## The allowlist is DEBT, not permission

Nine CLIs predate this guard. They are listed so the debt is countable and so a
NEW bypass cannot hide among them. The list may SHRINK, never grow. Adding an
entry to make a build pass is precisely the trap this file exists to close —
migrate the module instead; `post_earnings_puts` shows it is a small change.
"""
from __future__ import annotations

import os
import re

import pytest

CLI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tradepro_strategies", "cli")

# Raw-globbing the bar cache. DEBT — shrink this list, never extend it.
KNOWN_BYPASSES = {
    "swing_candidates.py",
    "momentum_candidates.py",
    "today_setups.py",
    "signal_audit.py",
    "name_context.py",
    "build_universe.py",
    "fill_replay.py",
    "paper_session.py",
    "check_daily_vs_intraday.py",
}

# A glob over a bar-cache partition path. The symbol segment may be an
# interpolation (`{sym}/1d/`) OR a wildcard (`*/5m/`) — the first version of
# this pattern required the interpolation and therefore missed
# check_daily_vs_intraday.py, which globs `{a.root}/*/5m/...`. A guard with a
# blind spot is worse than none, because the clean run is taken as proof.
_RES = r"(?:1d|5m|1m|15m|30m|1h)"
_BYPASS_PATTERNS = (
    # one call: .../<res>/*.parquet
    re.compile(rf"glob\.glob\(\s*f?\"[^\"]*{_RES}/[^\"]*\*?\.parquet"),
    # two calls: first list the RESOLUTION DIRECTORIES, then glob inside them.
    # check_daily_vs_intraday.py does exactly this — `{a.root}/*/5m` then
    # `{p}/{month}*.parquet` — so neither line carries both the resolution and
    # the extension, and a single-call pattern misses it entirely.
    re.compile(rf"glob\.glob\(\s*f?\"[^\"]*/{_RES}\"\s*\)"),
)


def _cli_modules():
    return sorted(f for f in os.listdir(CLI)
                  if f.endswith(".py") and not f.startswith("_"))


def _bypasses(fname: str) -> bool:
    with open(os.path.join(CLI, fname)) as fh:
        body = "\n".join(ln for ln in fh if not ln.lstrip().startswith("#"))
    return any(pat.search(body) for pat in _BYPASS_PATTERNS)


def test_no_new_module_reads_the_bar_cache_by_glob():
    """THE guard. A new bypass fails here, loudly, with the fix named."""
    offenders = [f for f in _cli_modules()
                 if f not in KNOWN_BYPASSES and _bypasses(f)]
    assert not offenders, (
        f"{offenders} read the bar cache with glob.glob(), bypassing "
        "BarStore._ensure_local and therefore the shared S3 store. That module "
        "will silently under-read wherever the local cache is incomplete.\n\n"
        "Use BarStore.get(..., skip_fetch=True) — local, falling through to S3, "
        "never calling a provider. See cli/post_earnings_puts.py::_bars.\n\n"
        "Do NOT add the file to KNOWN_BYPASSES to make this pass."
    )


def test_the_debt_list_is_accurate():
    """A file that no longer bypasses must LEAVE the list, so the count means
    something. Otherwise the allowlist rots into decoration and stops
    measuring anything."""
    stale = [f for f in KNOWN_BYPASSES
             if os.path.exists(os.path.join(CLI, f)) and not _bypasses(f)]
    assert not stale, (
        f"{stale} no longer bypass the store — remove them from KNOWN_BYPASSES. "
        "An allowlist that outlives its entries hides the next real one."
    )


def test_the_migrated_screener_stays_migrated():
    """post_earnings_puts was the first migration and is the worked example the
    failure message points at. If it regresses, the example is a lie."""
    assert not _bypasses("post_earnings_puts.py")
    with open(os.path.join(CLI, "post_earnings_puts.py")) as fh:
        src = fh.read()
    assert "BarStore" in src, "the screener must read through the store"
    assert "skip_fetch=True" in src, (
        "a SCREEN must never trigger provider fetches — that is the harvest's "
        "job, and it is how a screen ends up competing for the single IBKR "
        "market-data session"
    )


@pytest.mark.parametrize("fname", sorted(KNOWN_BYPASSES))
def test_known_bypasses_are_still_present_to_be_fixed(fname):
    """Documents the debt per-file so `pytest -v` lists what remains."""
    path = os.path.join(CLI, fname)
    if not os.path.exists(path):
        pytest.skip(f"{fname} has been deleted")
    assert _bypasses(fname)
