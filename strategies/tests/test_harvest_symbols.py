"""One definition of what the daily harvest refreshes.

`scripts/bar-cache-harvest-daily.sh` used to derive its own list by `ls`-ing the
bar-cache directory and re-implementing the instrument exclusions in grep. Two
things were wrong, and the first was silent for as long as it existed:

1. A NEW universe member was never harvested — no directory yet, so `ls` could
   not see it, so it got no daily bars until somebody noticed. The script's own
   comment conceded it: "adding new symbols is a separate seed step".
2. The shell pattern `^[A-Z0-9.-]+$` admits a `-USD` crypto pair, which
   `_instrument_ok` rejects. The two filters had already drifted.

The `ls` approach also once turned a mis-nested `us_etf/us_etf/` folder into a
phantom `US_ETF` symbol and marked 37+ consecutive daily harvests FAILED while
every real symbol was fine.
"""
from __future__ import annotations

import json
import os

import pytest

from tradepro_strategies.universe import harvest_symbols


@pytest.fixture()
def universe_of(tmp_path, monkeypatch):
    """Point the universe loader at a temp file holding the given symbols."""
    def _make(symbols: list[str]):
        p = tmp_path / "tradeable.json"
        p.write_text(json.dumps({
            "as_of": "2026-08-23T00:00:00+00:00",
            "symbols": [{"symbol": s} for s in symbols],
            "excluded": [],
        }))
        monkeypatch.setenv("TRADEPRO_UNIVERSE_PATH", str(p))
        return p
    return _make


def _store(tmp_path, dirs: list[str]):
    root = tmp_path / "us_etf"
    root.mkdir(exist_ok=True)
    for d in dirs:
        (root / d).mkdir(exist_ok=True)
    return root


def test_a_new_universe_member_is_harvested_even_with_no_store_directory(
        tmp_path, universe_of):
    """THE regression. Under the old `ls` derivation NEWCO could not be seen,
    so it silently received no daily bars."""
    universe_of(["AAPL", "NEWCO"])
    root = _store(tmp_path, ["AAPL"])          # NEWCO has never been harvested
    got = harvest_symbols(root)
    assert "NEWCO" in got, "a universe member with no store dir must still be harvested"
    assert "AAPL" in got


def test_names_that_dropped_out_of_the_universe_stay_fresh(tmp_path, universe_of):
    """Harvesting is not screening. A name near the liquidity floor crosses it
    in both directions, and re-seeding years of bars is expensive."""
    universe_of(["AAPL"])
    root = _store(tmp_path, ["AAPL", "DROPPED"])
    got = harvest_symbols(root)
    assert "DROPPED" in got


def test_instrument_filter_matches_the_rest_of_the_system(tmp_path, universe_of):
    """The shell regex admitted `-USD`; `_instrument_ok` does not. One filter."""
    universe_of(["AAPL"])
    root = _store(tmp_path, [
        "AAPL", "BTC-USD", "0700.HK", "CL=F", "^VIX",
    ])
    got = harvest_symbols(root)
    assert got == ["AAPL"], f"instrument filter leaked: {got}"


def test_the_asset_class_directory_cannot_masquerade_as_a_ticker(
        tmp_path, universe_of):
    """The phantom US_ETF symbol that failed 37 consecutive harvests."""
    universe_of(["AAPL"])
    root = _store(tmp_path, ["AAPL", "us_etf"])
    got = harvest_symbols(root)
    assert "US_ETF" not in got and "us_etf" not in got


def test_non_ticker_shaped_directories_are_ignored(tmp_path, universe_of):
    universe_of(["AAPL"])
    root = _store(tmp_path, ["AAPL", "some notes", "tmp$$", ".hidden"])
    got = harvest_symbols(root)
    assert got == ["AAPL"], got


def test_result_is_deduplicated_and_sorted(tmp_path, universe_of):
    universe_of(["MSFT", "AAPL"])
    root = _store(tmp_path, ["AAPL", "ZZZ"])
    got = harvest_symbols(root)
    assert got == sorted(set(got)) == ["AAPL", "MSFT", "ZZZ"]


def test_missing_store_directory_still_returns_the_universe(tmp_path, universe_of):
    """A gone store must not silently reduce the harvest to nothing — the
    universe is still authoritative about what we care about."""
    universe_of(["AAPL", "MSFT"])
    got = harvest_symbols(tmp_path / "does-not-exist")
    assert got == ["AAPL", "MSFT"]


def test_reproduces_the_live_derivation(universe_of):
    """Guard against a silent change in scope: against the REAL universe and the
    REAL store, the helper must return what the shell `ls` pipeline returned."""
    store = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
    if not os.path.isdir(store):
        pytest.skip("no local bar cache")
    got = harvest_symbols(store)
    assert got, "harvest set must never be empty against a populated store"
    assert not [s for s in got
                if "." in s or "=" in s or s.startswith("^") or s.endswith("-USD")]


def test_the_store_cannot_silently_widen_the_harvest(tmp_path, universe_of, monkeypatch):
    """A seed into the store must not redefine the nightly job's scope.

    2026-08-25: a broad seed on 24 Aug took the us_etf tree from 250 dirs to
    991. Because harvest_symbols unions universe with store, the nightly
    harvest went from 250 symbols to 955 without anyone choosing that. It ran
    an hour, served 113 of its first 114 from yfinance rather than IBKR, and
    died — the daily lane failed two nights running.

    Over the bound, fall back to the universe. It is the definition of what we
    trade; anything else is nice-to-have and must not be able to take the lane
    down.
    """
    monkeypatch.setenv("TRADEPRO_HARVEST_MAX_EXTRA", "3")
    universe_of(["AAA", "BBB"])
    root = _store(tmp_path, ["AAA", "BBB"] + [f"X{i}" for i in range(20)])
    got = harvest_symbols(root)
    assert got == ["AAA", "BBB"], f"store widened the harvest unchecked: {len(got)} symbols"


def test_a_few_dropped_names_are_still_kept_fresh(tmp_path, universe_of, monkeypatch):
    """The bound must not defeat the union's purpose for the normal case."""
    monkeypatch.setenv("TRADEPRO_HARVEST_MAX_EXTRA", "60")
    universe_of(["AAA", "BBB"])
    root = _store(tmp_path, ["AAA", "BBB", "DROPPED1", "DROPPED2"])
    got = harvest_symbols(root)
    assert got == ["AAA", "BBB", "DROPPED1", "DROPPED2"]
