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
    """The change-detection logic still works — it is simply OFF by default now.

    Phase 5 (1 Sep 2026) replaced four senders with one
    `tradepro-candidates-digest`, so TRADEPRO_WHEEL_EMAIL now defaults to "0".
    The logic below — alert when the ELIGIBLE SET changes rather than daily — is
    the good part of the old sender and the digest should grow it, so it stays
    tested rather than deleted.
    """
    from tradepro_strategies.cli import options_screen as osc
    monkeypatch.setenv("TRADEPRO_WHEEL_EMAIL", "1")
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


# ── Vol-regime floor: the KRE contradiction (bridge 1.35 at 2.4th pctile) ─
def test_vol_regime_percentile_low_when_iv_at_yearly_trough():
    from tradepro_strategies.cli.options_screen import vol_regime_percentile
    import math, random
    random.seed(7)
    # A year of lively vol (~35%) then a calm tail — current IV 16% sits
    # near the bottom of the yearly distribution.
    closes, px = [], 100.0
    for i in range(420):
        sigma = 0.35 if i < 340 else 0.12
        px *= math.exp(random.gauss(0, sigma / math.sqrt(252)))
        closes.append(px)
    p = vol_regime_percentile(closes, 0.11)
    assert p is not None and p < 15, p


def test_vol_regime_percentile_high_when_iv_rich():
    from tradepro_strategies.cli.options_screen import vol_regime_percentile
    import math, random
    random.seed(7)
    closes, px = [], 100.0
    for i in range(420):
        px *= math.exp(random.gauss(0, 0.20 / math.sqrt(252)))
        closes.append(px)
    p = vol_regime_percentile(closes, 0.60)   # IV far above the whole year
    assert p is not None and p > 85, p


def test_vol_regime_percentile_none_on_thin_history():
    from tradepro_strategies.cli.options_screen import vol_regime_percentile
    assert vol_regime_percentile([100.0] * 100, 0.25) is None


# ── Gap-contaminated HV (the IBM case, 13 Aug 2026) ─────────────────────
def _series_with_gap(gap_sessions_ago: int, n: int = 90, gap: float = -0.25):
    """Quiet ~1%/day series with ONE large gap `gap_sessions_ago` sessions
    from the end."""
    import math, random
    random.seed(3)
    rets = [random.gauss(0, 0.01) for _ in range(n)]
    rets[n - gap_sessions_ago] = gap
    px, closes = 100.0, [100.0]
    for r in rets:
        px *= math.exp(r)
        closes.append(px)
    return closes


def test_hv_gap_detected_and_rolloff_counted():
    from tradepro_strategies.cli.options_screen import hv_gap_diagnostics
    d = hv_gap_diagnostics(_series_with_gap(30))     # gap at the window edge
    assert d and d["contaminated"] is True
    assert d["gap_return_pct"] < -20
    # inflated raw HV, materially lower without the single session
    assert d["hv_raw"] > d["hv_ex_gap"] * 1.5
    # at the edge of a 30d window it rolls off almost immediately
    assert 1 <= d["sessions_until_rolloff"] <= 2


def test_hv_gap_none_on_quiet_series():
    from tradepro_strategies.cli.options_screen import hv_gap_diagnostics
    import math, random
    random.seed(5)
    px, closes = 100.0, [100.0]
    for _ in range(90):
        px *= math.exp(random.gauss(0, 0.012))
        closes.append(px)
    assert hv_gap_diagnostics(closes) is None


def test_hv_gap_rolloff_further_out_when_gap_is_recent():
    from tradepro_strategies.cli.options_screen import hv_gap_diagnostics
    d = hv_gap_diagnostics(_series_with_gap(3))      # gap 3 sessions ago
    assert d and d["sessions_until_rolloff"] > 20    # stays in the window a while
