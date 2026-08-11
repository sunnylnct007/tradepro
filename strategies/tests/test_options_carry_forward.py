"""Carry-forward pricing tier (owner priority, 10 Aug 2026).

A dark-MD run (post-close / one-MD-session contention) must keep the last
PRICED board — labeled with its age — instead of collapsing to "premium
unavailable". The rules that matter:

  • only rows priced from a real source qualify (live_mid /
    prev_close_indicative) — or a still-fresh carry, so carries CHAIN
    across runs (each push overwrites the stored payload);
  • the age cap is enforced against the ORIGINAL pricing time
    (premium_as_of_utc), never the latest push time;
  • premium-less or strike-less rows never qualify (nothing to carry).
"""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.cli.options_screen import build_carry_map

NOW = dt.datetime(2026, 8, 11, 14, 0, tzinfo=dt.timezone.utc)


def _payload(rows, generated_hours_ago=1.0):
    return {
        "generated_at_utc": (NOW - dt.timedelta(hours=generated_hours_ago)).isoformat(),
        "candidates": rows,
    }


def _row(**kw):
    base = {
        "symbol": "SLV",
        "suggested_strike": 54.0,
        "suggested_delta": 0.26,
        "suggested_premium": 1.32,
        "premium_source": "live_mid",
        "open_interest": 308,
        "spread_usd": 0.07,
        "dte": 39,
        "chain_source": "g3",
    }
    base.update(kw)
    return base


def test_live_mid_row_carries_with_push_time_as_asof():
    carry = build_carry_map(_payload([_row()], generated_hours_ago=5), now=NOW)
    assert "SLV" in carry
    assert carry["SLV"]["_carry_age_h"] == 5.0
    assert carry["SLV"]["premium_as_of_utc"] is not None


def test_prev_close_indicative_qualifies():
    carry = build_carry_map(
        _payload([_row(premium_source="prev_close_indicative")]), now=NOW)
    assert "SLV" in carry


def test_carried_row_chains_and_keeps_original_asof():
    original = (NOW - dt.timedelta(hours=20)).isoformat()
    carry = build_carry_map(
        _payload([_row(premium_source="carried_last_live",
                       premium_as_of_utc=original)],
                 generated_hours_ago=1),
        now=NOW)
    assert "SLV" in carry
    # age measured from the ORIGINAL pricing time, not the latest push
    assert round(carry["SLV"]["_carry_age_h"]) == 20


def test_age_cap_enforced_against_original_pricing_time():
    original = (NOW - dt.timedelta(hours=120)).isoformat()
    carry = build_carry_map(
        _payload([_row(premium_source="carried_last_live",
                       premium_as_of_utc=original)],
                 generated_hours_ago=1),   # pushed recently — must not rescue it
        now=NOW, max_age_h=96)
    assert carry == {}


def test_premiumless_and_strikeless_rows_never_carry():
    rows = [
        _row(suggested_premium=None),
        _row(symbol="AAPL", suggested_strike=None),
        _row(symbol="MSFT", premium_source=None, suggested_premium=2.0),
    ]
    assert build_carry_map(_payload(rows), now=NOW) == {}


def test_empty_or_missing_payload_is_empty_map():
    assert build_carry_map(None, now=NOW) == {}
    assert build_carry_map({}, now=NOW) == {}
