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


# ── Wheel alert email: fires ONLY when the eligible set changes ──────────
def _board(elig_syms, best=None):
    return {
        "generated_at_utc": "2026-08-11T19:00:00+00:00",
        "market_open": True,
        "best_symbol": best,
        "data_health": {"summary": "healthy"},
        "candidates": [
            {"symbol": s, "eligible": True, "suggested_strike": 54.0,
             "suggested_premium": 1.32, "premium_source": "live_mid",
             "annualized_yield_pct": 19.0, "suggested_delta": 0.26,
             "open_interest": 300, "dte": 39, "regime": "GREEN"}
            for s in elig_syms
        ],
    }


def test_wheel_email_fires_on_eligible_set_change(monkeypatch):
    from tradepro_strategies.cli import options_screen as osc
    sent = {}
    monkeypatch.setattr("tradepro_strategies.cli.email_digest.send_email",
                        lambda digest, cfg: sent.update(subject=digest.subject))
    monkeypatch.setattr("tradepro_strategies.cli.email_digest.CRED_PATH",
                        __import__("pathlib").Path("/nonexistent"))
    monkeypatch.setenv("TRADEPRO_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("TRADEPRO_SMTP_USER", "u")
    monkeypatch.setenv("TRADEPRO_SMTP_PASSWORD", "p")
    monkeypatch.setenv("TRADEPRO_EMAIL_FROM", "from@test")
    monkeypatch.setenv("TRADEPRO_EMAIL_TO", "to@test")
    assert osc._maybe_send_wheel_email(_board({"SLV"}, best="SLV"), _board(set())) is True
    assert "SLV" in sent["subject"] and "NEW eligible" in sent["subject"]


def test_wheel_email_silent_when_set_unchanged(monkeypatch):
    from tradepro_strategies.cli import options_screen as osc
    monkeypatch.setattr("tradepro_strategies.cli.email_digest.send_email",
                        lambda digest, cfg: (_ for _ in ()).throw(AssertionError("must not send")))
    assert osc._maybe_send_wheel_email(_board({"SLV"}), _board({"SLV"})) is False


def test_wheel_email_disabled_by_env(monkeypatch):
    from tradepro_strategies.cli import options_screen as osc
    monkeypatch.setenv("TRADEPRO_WHEEL_EMAIL", "0")
    assert osc._maybe_send_wheel_email(_board({"SLV"}), _board(set())) is False
