from tradepro_strategies.quant_engine.options.iv_rank import iv_rank_from_history


def test_iv_rank_mid_range():
    r = iv_rank_from_history("X", [0.10, 0.20, 0.30, 0.20])  # current 0.20 in 0.10-0.30
    assert r.available and abs(r.iv_rank - 50.0) < 1e-6
    assert r.low_52w == 0.10 and r.high_52w == 0.30


def test_iv_rank_below_gate_is_honest():
    # current near the low → low IV-rank → caller's >30 gate will skip (no false BUY)
    r = iv_rank_from_history("PFE", [0.18, 0.30, 0.21])  # 0.21 low in 0.18-0.30
    assert r.available and r.iv_rank < 30


def test_insufficient_history_unavailable():
    r = iv_rank_from_history("X", [0.2])
    assert not r.available and "insufficient" in r.reason


def test_drops_none_and_nonpositive():
    r = iv_rank_from_history("X", [None, 0.0, 0.10, 0.30, 0.10])
    assert r.available and r.days == 3
