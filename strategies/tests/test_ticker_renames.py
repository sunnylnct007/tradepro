"""Corporate-action ticker-rename canonicalisation.

Guards the live-trading bug where T212 reported a Bath & Body Works position
under the OLD ticker LB_US_EQ while the strategy universe/signal used the
CURRENT ticker BBWI — so the position dropped out of the seed, the "already
long?" guard went blind, and the strategy re-bought BBWI every cycle (only OMS
idempotency stopped duplicate fills).
"""
import importlib

import pytest

from tradepro_strategies.ticker_renames import (
    canonical_ticker,
    ticker_renames,
)
from tradepro_strategies.cli.paper_session import _parse_broker_position_rows


# ── canonical_ticker ────────────────────────────────────────────────

def test_known_renames_map_to_current_ticker():
    assert canonical_ticker("LB") == "BBWI"
    assert canonical_ticker("FB") == "META"


def test_unmapped_ticker_passes_through():
    assert canonical_ticker("AAPL") == "AAPL"
    assert canonical_ticker("BBWI") == "BBWI"  # already current — unchanged


def test_case_and_whitespace_normalised():
    assert canonical_ticker(" lb ") == "BBWI"
    assert canonical_ticker("fb") == "META"


def test_empty_input_is_safe():
    assert canonical_ticker("") == ""
    assert canonical_ticker(None) is None  # type: ignore[arg-type]


def test_env_override_registers_new_rename(monkeypatch):
    monkeypatch.setenv("TRADEPRO_TICKER_RENAMES", '{"OLD": "NEW"}')
    assert canonical_ticker("OLD") == "NEW"
    # built-ins still apply alongside the override
    assert canonical_ticker("LB") == "BBWI"
    assert "OLD" in ticker_renames()


def test_malformed_env_override_ignored(monkeypatch):
    monkeypatch.setenv("TRADEPRO_TICKER_RENAMES", "not-json{")
    # falls back to built-ins, does not raise
    assert canonical_ticker("LB") == "BBWI"
    assert canonical_ticker("OLD") == "OLD"


# ── _parse_broker_position_rows canonicalisation ────────────────────

def test_old_ticker_position_kept_under_current_ticker():
    # T212 reports the held BBWI position under the legacy LB_US_EQ code.
    rows = [{"ticker": "LB_US_EQ", "quantity": 34, "averagePricePaid": 27.0}]
    positions, avgs = _parse_broker_position_rows(rows, {"BBWI"})
    # Canonicalised to BBWI (the universe symbol) — NOT dropped as "LB".
    assert positions == {"BBWI": 34}
    assert avgs["BBWI"] == pytest.approx(27.0)
    assert "LB" not in positions


def test_old_and_current_ticker_rows_merge():
    # A legacy LB row + a fresh BBWI row are the SAME instrument — they must
    # net into one position, not two.
    rows = [
        {"ticker": "LB_US_EQ", "quantity": 10, "averagePricePaid": 20.0},
        {"ticker": "BBWI_US_EQ", "quantity": 24, "averagePricePaid": 30.0},
    ]
    positions, avgs = _parse_broker_position_rows(rows, {"BBWI"})
    assert positions == {"BBWI": 34}
    # |qty|-weighted blended cost basis: (20*10 + 30*24) / 34
    assert avgs["BBWI"] == pytest.approx((20.0 * 10 + 30.0 * 24) / 34)


def test_unmapped_ticker_outside_universe_still_filtered():
    # Regression guard: canonicalisation must not smuggle in a name that is
    # genuinely outside the universe.
    rows = [{"ticker": "NVDA_US_EQ", "quantity": 5, "averagePricePaid": 100.0}]
    positions, _ = _parse_broker_position_rows(rows, {"BBWI"})
    assert positions == {}


def test_no_universe_filter_still_canonicalises():
    # The held-symbols union calls with universe=None (no filter) — the old
    # ticker must still resolve to the current one so the union unions BBWI.
    rows = [{"ticker": "LB_US_EQ", "quantity": 34, "averagePricePaid": 27.0}]
    positions, _ = _parse_broker_position_rows(rows, None)
    assert positions == {"BBWI": 34}
