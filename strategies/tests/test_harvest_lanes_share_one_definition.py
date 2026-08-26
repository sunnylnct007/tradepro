"""Every harvest lane derives its symbols from ONE definition.

THE REGRESSION, 2026-08-26. `harvest_symbols()` was introduced on 25 Aug as the
single definition of what the nightly harvest refreshes, and
`bar-cache-harvest-daily.sh` was moved onto it. `bar-cache-harvest.sh` — the
script the 1m and 5m lanes run — was not. It kept deriving symbols by `ls`-ing
the cache directory.

The comment in the un-migrated copy asserted "Same derivation as
bar-cache-harvest-daily.sh". That sentence was true when written and false the
moment the other lane moved, which is the whole duplicate-definition failure
mode in one line: the same knowledge written twice, then one copy changes.

What it cost, from the run log:

    bar-cache-harvest 5m   955 sym → 0G/849S/106B/0M

955 symbols against a bounded 244 in the daily lane, because `ls` sees every
directory any one-off seed ever created and nothing bounded it. Zero GOLD: the
sweep could not finish inside its 60-minute deadline, so it never reached its
own completion line and the lane reported partial.

This test does not check that the two scripts agree today — that is what
drifted. It checks that neither can express its own opinion about scope.
"""
from __future__ import annotations

import os
import re

import pytest

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")

HARVEST_SCRIPTS = [
    "bar-cache-harvest.sh",           # the 1m / 5m lanes
    "bar-cache-harvest-daily.sh",     # the 1d lane
]


def _read(name: str) -> str:
    path = os.path.join(SCRIPTS, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} not present")
    with open(path) as fh:
        return fh.read()


@pytest.mark.parametrize("script", HARVEST_SCRIPTS)
def test_lane_calls_harvest_symbols(script):
    assert "harvest_symbols" in _read(script), (
        f"{script} must derive its scope from universe.harvest_symbols(), "
        "not from its own rules")


@pytest.mark.parametrize("script", HARVEST_SCRIPTS)
def test_lane_does_not_ls_the_cache_directory(script):
    """THE regression, stated as the shape rather than the symptom.

    An `ls` of the cache tree is a SECOND definition of scope no matter how
    carefully its greps are written, and it is unbounded by construction — it
    reports whatever a seed last left on disk.
    """
    body = _read(script)
    offenders = [
        ln.strip() for ln in body.splitlines()
        if re.search(r'\bls\s+"?\$(CACHE_DIR|STORE)', ln)
        and not ln.strip().startswith("#")
    ]
    assert not offenders, (
        f"{script} derives symbols by listing the cache directory: {offenders}")


@pytest.mark.parametrize("script", HARVEST_SCRIPTS)
def test_lane_fails_loud_on_an_empty_scope(script):
    """Harvesting nothing while reporting success is how a lane goes quietly
    dark. Both lanes must exit non-zero rather than fall back to the
    harvester's 12-mega-cap built-in list, which looks like a working sweep.
    """
    body = _read(script)
    assert re.search(r"FATAL.*harvest_symbols", body), (
        f"{script} must fail loudly when harvest_symbols() returns nothing")


def test_the_bound_actually_binds(tmp_path, monkeypatch):
    """The property `ls` could not have: scope is bounded by the universe.

    On 26 Aug the store held 711 directories beyond the 244-name universe.
    Unbounded, that is what the 5m lane swept.
    """
    import json

    from tradepro_strategies.universe import harvest_symbols

    uni = tmp_path / "tradeable.json"
    uni.write_text(json.dumps({
        "as_of": "2026-08-26T00:00:00+00:00",
        "symbols": [{"symbol": "AAA"}, {"symbol": "BBB"}],
        "excluded": [],
    }))
    monkeypatch.setenv("TRADEPRO_UNIVERSE_PATH", str(uni))
    monkeypatch.setenv("TRADEPRO_HARVEST_MAX_EXTRA", "60")

    root = tmp_path / "us_etf"
    root.mkdir()
    for d in ["AAA", "BBB"] + [f"SEED{i}" for i in range(711)]:
        (root / d).mkdir()

    got = harvest_symbols(root)
    assert got == ["AAA", "BBB"], (
        f"a broad seed widened the harvest to {len(got)} symbols unchecked")
