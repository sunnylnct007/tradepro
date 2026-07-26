"""Entry-quality gate — don't BUY a laggard on no participation (the ANET case).

Guards feedback_no_false_positives: a BUY on weak RS or thin volume is demoted,
and a BUY we CANNOT check (missing RS/volume) is capped, never waved through as a
clean high-conviction rec.
"""
from tradepro_strategies.gates.entry_quality import (
    EntryQualityConfig,
    evaluate_entry_quality,
)

CFG = EntryQualityConfig()   # min_rs=5, min_volume_ratio=0.8


def _e(rs, vol):
    return evaluate_entry_quality(rs_score=rs, volume_ratio=vol, cfg=CFG)


def test_strong_rs_and_volume_passes():
    g = _e(7, 1.3)
    assert g.passed and g.action == "clear" and not g.reasons


def test_anet_case_weak_rs_thin_volume_vetoed():
    # ANET: RS ~2/10, volume ~0.5× → the entry the gate must block.
    g = _e(2, 0.5)
    assert not g.passed and g.action == "veto"
    assert any("relative strength" in r for r in g.reasons)
    assert any("thin volume" in r for r in g.reasons)


def test_weak_rs_alone_vetoes():
    g = _e(3, 1.5)
    assert g.action == "veto" and any("relative strength" in r for r in g.reasons)


def test_thin_volume_alone_vetoes():
    g = _e(8, 0.6)
    assert g.action == "veto" and any("thin volume" in r for r in g.reasons)


def test_boundary_values_pass():
    # Exactly at the floors → not below → clear.
    g = _e(5, 0.8)
    assert g.passed and g.action == "clear"


def test_missing_rs_flags_not_veto_not_pass():
    # Can't check RS → cap, never a clean BUY, but not an active veto either.
    g = _e(None, 1.2)
    assert not g.passed and g.action == "flag_missing"
    assert any("RS" in r and "unavailable" in r for r in g.reasons)


def test_missing_both_flags_missing():
    g = _e(None, None)
    assert not g.passed and g.action == "flag_missing"


def test_failing_floor_beats_missing_other():
    # RS fails AND volume missing → the hard veto wins (still demote).
    g = _e(2, None)
    assert g.action == "veto"
