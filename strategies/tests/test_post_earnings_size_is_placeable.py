"""A screen that tells you what to place must show what CAN be placed.

30 Aug 2026, from a live board: MRVL showed "Sell put at 194.96, size 0.34x,
collateral $6,663". Two of those three numbers were unplaceable.

  * You cannot sell 0.34 of a contract. The real minimum is ONE contract at
    $19,500 — the screen understated capital at risk by 2.9x, and understating
    is the dangerous direction: it reads as a small, well-sized position while
    committing three times the collateral.
  * 194.96 is not a listed strike. Same class as the BANKNIFTY "56,697.84 PUT"
    fixed the same day: printing an untradeable price in something that asks
    someone to place a trade destroys confidence in everything beside it.
"""
from __future__ import annotations

from tradepro_strategies.cli.post_earnings_puts import _snap_strike, _tradeable_size


def test_strike_snaps_to_a_listed_increment():
    assert _snap_strike(194.96) == 195.00      # $2.50 grid under $200
    assert _snap_strike(216.62) == 215.00      # $5 grid over $200
    for raw in (12.3, 47.9, 194.96, 216.62, 501.4):
        snapped = _snap_strike(raw)
        step = 2.5 if snapped < 200 else 5.0
        assert abs(snapped / step - round(snapped / step)) < 1e-9, raw


def test_a_fractional_size_becomes_one_whole_contract():
    """The exact MRVL row that exposed this."""
    out = _tradeable_size(194.96, 0.34)
    assert out["contracts"] == 1
    assert out["collateral_actual_usd"] == 19500      # 195.00 x 100 x 1
    assert out["collateral_target_usd"] == 6630       # what the vol rule asked


def test_collateral_never_restricts_a_candidate():
    """Owner, 30 Aug 2026: "I do not want collateral restrictions", consistent
    with the standing rule that capital never decides eligibility.

    This function must REPORT and never judge. If a warning, a demotion or a
    filter ever reappears here, it contradicts an explicit instruction and this
    fails."""
    out = _tradeable_size(194.96, 0.34)
    for banned in ("size_note", "oversize_vs_target", "blocked", "warning"):
        assert banned not in out, f"{banned} reintroduces a capital judgement"
    assert set(out) == {"strike_indicative", "contracts",
                        "collateral_actual_usd", "collateral_target_usd"}


def test_a_name_that_sizes_properly_carries_no_warning():
    out = _tradeable_size(50.0, 2.0)
    assert out["contracts"] == 2
    assert out["collateral_actual_usd"] == 10000


def test_collateral_is_never_below_one_contract():
    """The invariant. Any factor, any strike — you cannot place less than one."""
    for strike in (10.0, 87.5, 194.96, 640.0):
        for factor in (0.01, 0.34, 0.9, 1.0, 2.0):
            out = _tradeable_size(strike, factor)
            assert out["contracts"] >= 1
            assert out["collateral_actual_usd"] >= out["strike_indicative"] * 100
