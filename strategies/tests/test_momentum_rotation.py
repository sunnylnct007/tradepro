"""The momentum rotation engine follows the pre-registered rules
(MOMENTUM_ROTATION_GATES_V1.md), checked on synthetic panels where the right
answer is known in advance."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradepro_strategies import momentum_rotation as M

N = 700  # sessions: enough for 252-day lookbacks and a 200-day average


def _panels(drifts: dict[str, float], spy_drift=0.0005, sectors=None, shock=None):
    dates = pd.bdate_range("2019-01-01", periods=N)
    cols = {}
    for s, d in drifts.items():
        px = 100 * np.exp(np.cumsum(np.full(N, d)))
        if shock and s in shock:
            day, factor = shock[s]
            px[day:] *= factor
        cols[s] = px
    adj = pd.DataFrame(cols, index=dates)
    sd = np.full(N, spy_drift) if np.isscalar(spy_drift) else np.asarray(spy_drift)
    spy = pd.Series(100 * np.exp(np.cumsum(sd)), index=dates)
    vol = pd.DataFrame(1e6, index=dates, columns=adj.columns)
    return M.Panels(dates=dates, adj_close=adj, adj_open=adj, raw_close=adj, volume=vol,
                    spy=spy, sectors=sectors or {s: "S" + s for s in drifts})


def _p(**kw):
    base = dict(start="2020-03-01", top_n=2, vol_target=None, trailing_stop=None,
                alert_halve=None, alert_cash=None, min_traded_value=1.0)
    base.update(kw)
    return M.with_overrides(**base)


def test_holds_the_strongest_names():
    P = _panels({"A": 0.002, "B": 0.0015, "C": 0.0005, "D": 0.0001})
    r = M.backtest(_p(), P)
    assert r["rebalances"][-1]["picks"] == ["A", "B"]


def test_market_filter_goes_to_cash():
    down = np.r_[np.full(N - 150, 0.0005), np.full(150, -0.004)]
    P = _panels({"A": 0.002, "B": 0.0015}, spy_drift=down)
    r = M.backtest(_p(), P)
    last = r["rebalances"][-1]
    assert last["picks"] == [] and "market filter" in last["reason"]
    assert r["activity"]["time_in_cash_pct"] > 0


def test_sector_cap_skips_to_the_next_name():
    P = _panels({"A": 0.003, "B": 0.0025, "C": 0.0004},
                sectors={"A": "Tech", "B": "Tech", "C": "Energy"})
    r = M.backtest(_p(sector_cap=1), P)
    assert r["rebalances"][-1]["picks"] == ["A", "C"]


def test_trailing_stop_exits_a_crashing_holding():
    P = _panels({"A": 0.002, "B": 0.0015, "C": 0.0001}, shock={"A": (N - 40, 0.6)})
    r = M.backtest(_p(), P)
    assert r["activity"]["trailing_stop_exits"] == 0
    r = M.backtest(_p(trailing_stop=0.20), P)
    assert r["activity"]["trailing_stop_exits"] >= 1


def test_allocation_never_exceeds_the_base_and_vol_target_shrinks_it():
    P = _panels({"A": 0.002, "B": 0.0015})
    r = M.backtest(_p(), P)
    assert max(x["allocation"] for x in r["rebalances"]) <= 0.5
    noisy = _panels({"A": 0.002, "B": 0.0015})
    rng = np.random.default_rng(0)
    noisy.adj_close = noisy.adj_close * np.exp(rng.normal(0, 0.05, noisy.adj_close.shape))
    noisy.adj_open = noisy.adj_close
    r = M.backtest(_p(vol_target=0.20), noisy)
    assert any(x["allocation"] < 0.5 for x in r["rebalances"] if x["picks"])


def test_signal_day_is_the_15th_or_the_last_session_before_it():
    d = pd.bdate_range("2026-01-01", "2026-03-31")
    got = M.rebalance_dates(d, M.Params())
    assert [str(x.date()) for x in got] == ["2026-01-15", "2026-02-13", "2026-03-13"]


def test_a_null_override_turns_a_rule_off_and_a_typo_is_refused():
    assert M.with_overrides(trailing_stop=None).trailing_stop is None
    import pytest
    with pytest.raises(ValueError):
        M.with_overrides(trailing_stp=0.1)
