"""Exposure by segment in dollar-delta (options review item 1, 28 Nov 2026).
Synthetic book — no owner figures."""
from datetime import date

from tradepro_strategies.live_exposure import (
    ExposureConfig, bs_delta_from_mark, exposure, legs_from_positions)

TODAY = date(2026, 11, 27)
CFG = ExposureConfig.from_kv({
    "segments": {"semis": ["AAA", "BBB"], "software": ["CCC"]},
    "segment_hedge_etf": {"semis": "SMH", "software": "IGV"},
    "sector_cap_pct": {"low": 40, "high": 45},
}, sector_of={"DDD": "healthcare"})
SPOTS = {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0, "DDD": 80.0, "SMH": 400.0}


def _book():
    return [
        {"symbol": "AAA", "quantity": 300},                                    # shares: +30,000
        {"symbol": "AAA   270115P00090000", "quantity": -2, "markPrice": 3.0},  # short put Δ-0.30 → +6,000
        {"symbol": "BBB   270115C00060000", "quantity": -1, "markPrice": 1.0},  # short call Δ+0.25 → -1,250
        {"symbol": "CCC", "quantity": 100},                                    # +20,000 software
        {"symbol": "SMH   261218P00390000", "quantity": 4, "markPrice": 10.0},  # long put hedge Δ-0.40 → -64,000
        {"symbol": "DDD", "quantity": 50},                                     # +4,000 healthcare (sector fallback)
    ]


def _delta(root, expiry, right, strike):
    return {("AAA", "P"): -0.30, ("BBB", "C"): 0.25, ("SMH", "P"): -0.40}.get((root, right))


def test_delta_dollars_by_segment_with_hedges_attributed():
    legs = legs_from_positions(_book(), spots=SPOTS, delta_for=_delta, cfg=CFG, today=TODAY)
    x = exposure(legs, cfg=CFG, nav_usd=200_000, gbp_per_usd=0.79)
    seg = {s["segment"]: s for s in x["segments"]}
    assert seg["semis"]["long_usd"] == 30_000 + 6_000 - 1_250
    assert seg["semis"]["hedge_usd"] == -64_000
    assert seg["software"]["long_usd"] == 20_000 and seg["healthcare"]["long_usd"] == 4_000
    assert x["gross_long_usd"] == 34_750 + 20_000 + 4_000
    assert x["hedges_usd"] == -64_000 and x["net_usd"] == 58_750 - 64_000
    assert x["one_pct_move_usd"] == round((58_750 - 64_000) / 100)
    assert x["legs_priced"] == 6 and x["gaps"] == []
    assert x["delta_sources"] == {"shares": 3, "chain": 3, "black_scholes(mark)": 0, "missing": 0}


def test_the_cap_flag_names_the_segment_and_the_number():
    legs = legs_from_positions(_book(), spots=SPOTS, delta_for=_delta, cfg=CFG, today=TODAY)
    x = exposure(legs, cfg=CFG, nav_usd=200_000)
    semis = next(s for s in x["segments"] if s["segment"] == "semis")
    assert semis["over_cap"] is True and semis["share_of_gross_long_pct"] == 59.1
    assert any("semis is 59%" in f and "45% cap" in f for f in x["flags"])


def test_a_missing_delta_is_a_named_gap_not_a_zero():
    legs = legs_from_positions(_book(), spots=SPOTS, delta_for=lambda *a: None, cfg=CFG, today=TODAY)
    # no chain deltas → solved from the mark, so still priced
    assert all(l.delta is not None for l in legs)
    assert {l.delta_source for l in legs if l.kind != "stock"} == {"black_scholes(mark)"}
    # no mark either → gap
    book = _book(); book[1].pop("markPrice")
    legs = legs_from_positions(book, spots=SPOTS, delta_for=lambda *a: None, cfg=CFG, today=TODAY)
    x = exposure(legs, cfg=CFG, nav_usd=None)
    assert x["legs_missing"] == 1 and x["gaps"][0]["symbol"] == "AAA" and x["gaps"][0]["missing"] == "delta"
    assert x["net_pct_of_nav"] is None


def test_a_missing_spot_is_a_gap_too():
    spots = dict(SPOTS); spots["CCC"] = None
    legs = legs_from_positions(_book(), spots=spots, delta_for=_delta, cfg=CFG, today=TODAY)
    x = exposure(legs, cfg=CFG, nav_usd=200_000)
    assert [g["symbol"] for g in x["gaps"]] == ["CCC"] and x["gaps"][0]["missing"] == "spot"
    assert "software" not in {s["segment"] for s in x["segments"]}


def test_black_scholes_delta_from_mark_is_signed_and_bounded():
    d = bs_delta_from_mark(mark=3.0, spot=100.0, strike=90.0, expiry="2027-01-15", right="P", today=TODAY)
    assert d is not None and -0.6 < d < -0.05
    c = bs_delta_from_mark(mark=4.0, spot=100.0, strike=105.0, expiry="2027-01-15", right="C", today=TODAY)
    assert c is not None and 0.1 < c < 0.7
    assert bs_delta_from_mark(mark=None, spot=100.0, strike=90.0, expiry="2027-01-15", right="P", today=TODAY) is None
    assert bs_delta_from_mark(mark=0.5, spot=100.0, strike=90.0, expiry="2026-11-27", right="P", today=TODAY) == 0.0


def test_the_module_cannot_place_orders():
    import inspect
    import tradepro_strategies.live_exposure as M
    src = inspect.getsource(M)
    assert "oms/orders" not in src and "placeOrder" not in src and "requests." not in src


def test_the_tool_is_on_mcp_and_reads_the_artifacts_exposure_block(monkeypatch):
    """Owner, 28 Nov 2026: every capability is exposed over MCP."""
    import pathlib
    from tradepro_strategies.mcp import tools as T
    src = (pathlib.Path(T.__file__).parent / "server.py").read_text()
    assert '@instrumented("live_book_exposure")' in src
    monkeypatch.setattr(T, "_get", lambda path, **k: {"artifact": {"as_of_utc": "x", "report_date": "2026-11-27",
                                                                    "exposure": {"net_usd": 1.0}}})
    out = T.live_book_exposure()
    assert out["ok"] is True and out["exposure"]["net_usd"] == 1.0 and out["placed_orders"] == 0
    monkeypatch.setattr(T, "_get", lambda path, **k: {"artifact": {"as_of_utc": "x"}})
    assert T.live_book_exposure()["ok"] is False


def test_live_risk_attaches_exposure_without_touching_the_verdicts(monkeypatch):
    from tradepro_strategies.cli import live_risk as L
    positions = [{"symbol": "AAA", "quantity": 100, "reportDate": "2026-11-27"},
                 {"symbol": "AAA   270115P00090000", "quantity": -1, "markPrice": 3.0, "costBasisPrice": 4.0}]
    monkeypatch.setattr(L, "_spots", lambda syms: {s: 100.0 for s in syms})
    monkeypatch.setattr(L, "_sector_map", lambda: {"AAA": "semis"})
    import tradepro_strategies.income_spreads as I
    monkeypatch.setattr(I, "_kv_get", lambda b, t, k: {} if k == "risk_guardrails" else {"fx_gbp_per_usd_fallback": 0.8})
    class _R:
        status_code = 200
        def json(self): return {"legs": [{"strike": 90.0, "right": "P", "delta": -0.3}]}
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _R())
    out = L._exposure_for(positions, "http://x", "t", nav_usd=50_000)
    assert out["net_usd"] == 100 * 100 + 3000 and out["delta_sources"]["chain"] == 1
    assert out["segments"][0]["segment"] == "semis" and out["one_pct_move_gbp"] == round(13000 / 100 * 0.8)
