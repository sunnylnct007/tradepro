"""FOMC stress test (review item 14). Synthetic exposure — no owner figures."""
from datetime import date

from tradepro_strategies.stress_test import (DEFAULT_SCENARIOS, implied_move_from_straddle,
                                             run_scenario, segment_pnl, stress)

ETF = {"seg_a": "SMH", "seg_b": "IGV"}     # seg_c has no hedge ETF → the default move
EXPO = {"segments": [
    {"segment": "seg_a", "net_usd": 100_000.0, "gamma_usd_per_1pct": -800.0, "gamma_unknown_legs": 0},
    {"segment": "seg_b", "net_usd": 40_000.0, "gamma_usd_per_1pct": -200.0, "gamma_unknown_legs": 0},
    {"segment": "seg_c", "net_usd": 10_000.0, "gamma_usd_per_1pct": None, "gamma_unknown_legs": 1},
]}


def test_segment_pnl_has_first_and_second_order_terms():
    r = segment_pnl(EXPO["segments"][0], -7.0)
    assert r["first_order_usd"] == -7_000 and r["gamma_usd"] == round(0.5 * -800 * 49) == -19_600
    assert r["pnl_usd"] == -26_600
    assert segment_pnl(EXPO["segments"][2], -2.5)["gamma_usd"] == 0 and segment_pnl(EXPO["segments"][2], -2.5)["gamma_unknown_legs"] == 1


def test_hawkish_shock_uses_named_moves_and_the_default_for_the_rest():
    r = run_scenario(EXPO, DEFAULT_SCENARIOS["hawkish_shock"], segment_to_etf=ETF)
    by = {x["segment"]: x for x in r["segments"]}
    assert by["seg_a"]["move_pct"] == -7.0 and by["seg_b"]["move_pct"] == -5.0 and by["seg_c"]["move_pct"] == -2.5
    assert r["pnl_usd"] == -26_600 + (-2_000 + round(0.5 * -200 * 25)) + (-250)


def test_floor_test_in_gbp_and_flags():
    out = stress(EXPO, nav_usd=250_000, floor_gbp=180_000, gbp_per_usd=0.79, today=date(2026, 11, 28), segment_to_etf=ETF)
    hs = out["scenarios"]["hawkish_shock"]
    assert hs["nav_after_usd"] == 250_000 + hs["pnl_usd"]
    assert out["floor_usd"] == round(180_000 / 0.79)
    assert hs["breaches_floor"] is True and any("BELOW the floor" in f for f in out["flags"])
    assert out["scenarios"]["base"]["pnl_usd"] == 0 and out["scenarios"]["base"]["breaches_floor"] is False
    assert any("without gamma" in g for g in out["gaps"])


def test_without_nav_or_floor_it_says_so_rather_than_guessing():
    out = stress(EXPO, nav_usd=None, floor_gbp=None, gbp_per_usd=None)
    assert out["scenarios"]["hawkish_shock"]["nav_after_usd"] is None and out["scenarios"]["hawkish_shock"]["breaches_floor"] is None
    assert any("not supplied" in g for g in out["gaps"]) and any("floor" in g for g in out["gaps"])
    assert out["hike_odds"]["available"] is False and out["placed_orders"] == 0


def test_betas_apply_when_a_scenario_names_no_segment_move():
    sc = {"label": "x", "index_move_pct": -4.0, "moves": {}}
    r = run_scenario(EXPO, sc, betas={"seg_a": 1.6})
    by = {x["segment"]: x for x in r["segments"]}
    assert by["seg_a"]["move_pct"] == -6.4 and by["seg_c"]["move_pct"] == -4.0


def test_implied_move_from_the_atm_straddle():
    assert implied_move_from_straddle(10.0, 9.0, 650.0) == round(19 / 650 * 100, 2)
    assert implied_move_from_straddle(None, 9.0, 650.0) is None


def test_the_module_cannot_place_orders():
    import inspect
    import tradepro_strategies.stress_test as M
    src = inspect.getsource(M)
    assert "oms/orders" not in src and "requests." not in src


def test_live_risk_attaches_the_stress_block_and_mcp_serves_it(monkeypatch):
    from tradepro_strategies.cli import live_risk as L
    import tradepro_strategies.income_spreads as I
    kv = {"risk_guardrails": {"account_floor_gbp": 180_000, "segment_hedge_etf": ETF}, "income_engine": {"fx_gbp_per_usd_fallback": 0.79},
          "macro": {"fomc_hike_odds_pct": 90, "fomc_hike_odds_as_of": "2026-11-27"}}
    monkeypatch.setattr(I, "_kv_get", lambda b, t, k: kv.get(k, {}))
    class _R:
        status_code = 200
        def json(self): return {"spot": 650.0, "legs": [{"strike": 650.0, "right": "C", "bid": 9.0, "ask": 11.0, "maturityDate": "20261211"},
                                                       {"strike": 650.0, "right": "P", "bid": 8.0, "ask": 10.0, "maturityDate": "20261211"}]}
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _R())
    out = L._stress_for(EXPO, "http://x", "t", nav_usd=250_000)
    assert out["version"] == "FOMC_STRESS_V1" and out["scenarios"]["hawkish_shock"]["breaches_floor"] is True
    assert out["hike_odds"]["available"] is True and out["hike_odds"]["hike_odds_pct"] == 90
    assert out["implied_moves"]["QQQ"]["move_pct"] == round(19 / 650 * 100, 2)
    assert any(e.get("kind") == "fomc" for e in out["events"])     # 9 Dec is in the market calendar
    import pathlib
    from tradepro_strategies.mcp import tools as T
    src = (pathlib.Path(T.__file__).parent / "server.py").read_text()
    assert '@instrumented("fomc_stress")' in src
    monkeypatch.setattr(T, "_get", lambda path, **k: {"artifact": {"stress": {"version": "FOMC_STRESS_V1"}}})
    assert T.fomc_stress()["ok"] is True and T.fomc_stress()["placed_orders"] == 0
