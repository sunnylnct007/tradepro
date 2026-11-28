"""Put-book manager (review item 17). Synthetic rows — no owner figures."""
from datetime import date

from tradepro_strategies.put_book import (cash_calendar, cheap_exit, classify, flag_for,
                                          hedge_coverage_gap, put_book)

KV = {"want_to_own": {"AAA": "want", "BBB": "no_more_beyond_existing"},
      "cash_reserve_usd": 10_000, "cash_available_usd": 50_000}


def _row(sym, strike, credit, spot, dte, *, expiry="2027-01-15", structure="naked", qty=-1, events=None, mark=None):
    return {"symbol": sym, "contract": f"{sym} {expiry} {strike}P", "strike": strike, "credit": credit, "spot": spot,
            "dte": dte, "expiry": expiry, "structure": structure, "qty": qty, "mark": mark if mark is not None else credit,
            "obligation_usd": strike * 100 * abs(qty) if structure == "naked" else 10 * 100 * abs(qty),
            "events": events or {"events": [], "names": ""}, "action": "HOLD", "rule": ""}


def test_rules_in_order():
    earn = {"events": [{"kind": "earnings", "title": "AAA earnings"}], "names": "AAA earnings"}
    assert classify(_row("AAA", 100, 4, 110, 20, events=earn), flag="want", cash_ok=True, name_cap_ok=True, earnings_inside=True)[0] == "CLOSE"
    assert classify(_row("BBB", 100, 4, 110, 60), flag="no_more", cash_ok=True, name_cap_ok=True, earnings_inside=False)[0] == "REPAIR"
    assert classify(_row("AAA", 100, 4, 97, 60), flag="want", cash_ok=True, name_cap_ok=True, earnings_inside=False)[0] == "HOLD"       # gap +1%
    p, why = classify(_row("AAA", 100, 4, 90, 60), flag="want", cash_ok=True, name_cap_ok=True, earnings_inside=False)
    assert p == "HOLD/ACCEPT" and "rule 4" in why                                                                                     # gap −6%
    p, why = classify(_row("AAA", 100, 4, 90, 60), flag="want", cash_ok=None, name_cap_ok=True, earnings_inside=False)
    assert p == "HOLD" and "cash available unknown" in why
    assert classify(_row("AAA", 100, 4, 80, 60), flag="want", cash_ok=True, name_cap_ok=True, earnings_inside=False)[0] == "REPAIR"    # gap −17%
    assert classify(_row("CCC", 100, 4, 90, 60), flag="neutral", cash_ok=True, name_cap_ok=True, earnings_inside=False)[0] == "HOLD"
    assert classify(_row("AAA", 100, 4, 90, 60, structure="credit_spread"), flag="want", cash_ok=True, name_cap_ok=True, earnings_inside=False)[0] == "HOLD"
    assert classify(_row("AAA", 100, None, 90, 60), flag="want", cash_ok=True, name_cap_ok=True, earnings_inside=False)[0] == "UNKNOWN"


def test_cheap_exit_triggers_and_the_sector_down_day_suppression():
    r = _row("AAA", 100, 4, 95, 60, mark=0.9)
    ce = cheap_exit(r, {"low_10d": 86.0, "sma20": 95.3}, sector_down_pct=-0.5)
    assert ce["now"] is True and len(ce["reasons"]) == 3
    ce = cheap_exit(r, {"low_10d": 86.0}, sector_down_pct=-3.5)
    assert ce["now"] is False and "no repair close on a down day" in ce["suppressed"]
    assert cheap_exit(_row("AAA", 100, 4, 95, 60, mark=3.9), None, sector_down_pct=None)["now"] is False


def test_cash_calendar_flags_the_short_month_and_ranks_by_gap():
    rows = [_row("AAA", 300, 5, 290, 50, expiry="2027-01-15"), _row("CCC", 250, 5, 230, 50, expiry="2027-01-15"),
            _row("DDD", 100, 2, 120, 80, expiry="2027-02-19")]
    gaps = {rows[0]["contract"]: -1.7, rows[1]["contract"]: -6.1, rows[2]["contract"]: 22.4}
    cal = cash_calendar(rows, cash_available_usd=50_000, cash_reserve_usd=10_000, gap_of=gaps)
    jan = next(m for m in cal["months"] if m["month"] == "2027-01")
    assert jan["assignment_usd"] == 55_000 and jan["status"] == "SHORT" and jan["short_by_usd"] == 15_000
    assert jan["propose_closing_ahead"] == ["CCC"]                       # the worse gap first
    feb = next(m for m in cal["months"] if m["month"] == "2027-02")
    assert feb["status"] == "covered"
    unknown = cash_calendar(rows, cash_available_usd=None, cash_reserve_usd=10_000, gap_of=gaps)
    assert all(m["status"].startswith("unknown") for m in unknown["months"]) and unknown["note"]


def test_hedge_coverage_gap_names_the_months_without_a_hedge():
    rows = [_row("AAA", 100, 4, 95, 50, expiry="2026-12-18"), _row("CCC", 100, 4, 95, 100, expiry="2027-03-19")]
    positions = [{"symbol": "SMH   261218P00390000", "quantity": 4}, {"symbol": "AAA", "quantity": 100}]
    g = hedge_coverage_gap(positions, rows)
    assert g["last_hedge_expiry"] == "2026-12-18" and g["put_expiries_after_hedges_end"] == ["2027-03-19"]
    assert "hedges end 2026-12-18" in g["note"]


def test_put_book_end_to_end_sorts_act_first_and_never_places():
    earn = {"events": [{"kind": "earnings", "title": "EEE earnings"}, {"kind": "fomc", "title": "FOMC"}], "names": "EEE earnings · FOMC"}
    rows = [_row("AAA", 100, 4, 90, 60), _row("BBB", 50, 2, 55, 60), _row("EEE", 200, 6, 210, 20, expiry="2026-12-18", events=earn),
            _row("AAA", 90, 3, 90, 60, structure="hedge_short_leg")]
    bars = {"AAA": {"low_10d": 82.0, "sma20": 92.0, "low_20d": 85.0}, "BBB": {"low_10d": 50.0, "low_20d": 48.0}, "EEE": None}
    out = put_book(rows, positions=[], kv=KV, nav_usd=500_000, bars_for=lambda s: bars.get(s), sector_down_pct_of=lambda s: -1.0, today=date(2026, 11, 28))
    pol = {r["symbol"]: r["policy"] for r in out["rows"]}
    assert pol == {"EEE": "CLOSE", "BBB": "REPAIR", "AAA": "HOLD/ACCEPT"}
    assert [r["symbol"] for r in out["rows"]] == ["EEE", "BBB", "AAA"]        # act first
    assert len(out["rows"]) == 3                                              # the hedge leg is not a put-book row
    aaa = out["rows"][2]
    assert aaa["cheap_exit"]["now"] is True and aaa["floor"]["support"] == 85.0 and aaa["floor"]["below_support"] is False
    assert out["cash_calendar"]["free_cash_usd"] == 40_000 and out["placed_orders"] == 0
    assert out["counts"] == {"CLOSE": 1, "REPAIR": 1, "HOLD/ACCEPT": 1}


def test_flags_collapse_any_no_more_variant():
    assert flag_for("bbb", KV["want_to_own"]) == "no_more" and flag_for("AAA", KV["want_to_own"]) == "want"
    assert flag_for("ZZZ", KV["want_to_own"]) == "neutral" and flag_for("ZZZ", None) == "neutral"


def test_the_module_cannot_place_orders():
    import inspect
    import tradepro_strategies.put_book as M
    src = inspect.getsource(M)
    assert "oms/orders" not in src and "requests." not in src and "placeOrder" not in src


def test_live_risk_attaches_the_put_book_and_mcp_serves_it(monkeypatch):
    from tradepro_strategies.cli import live_risk as L
    import tradepro_strategies.income_spreads as I
    monkeypatch.setattr(I, "_kv_get", lambda b, t, k: KV)
    import pandas as pd
    idx = pd.date_range("2026-10-01", periods=40, freq="B")
    df = pd.DataFrame({"close": [100.0] * 39 + [95.0], "low": [99.0] * 39 + [94.0]}, index=idx)
    import tradepro_strategies.daily_bars as D
    monkeypatch.setattr(D, "load_bars", lambda sym, res, lookback_days=60: df)
    import tradepro_strategies.sector_rs as S
    monkeypatch.setattr(S, "get_sector_etf", lambda sym: ("XLK", False))
    rows = [_row("AAA", 100, 4, 95, 60)]
    out = L._put_book_for(rows, [{"symbol": "AAA", "quantity": 0}], "http://x", "t", nav_usd=500_000)
    # gap −1%, want-to-own, free cash 40k ≥ 10k obligation, name cap holds → HOLD/ACCEPT;
    # the sector ETF is −5% today, so no cheap-exit repair is raised on this day
    assert out["version"] == "PUT_BOOK_V1" and out["rows"][0]["policy"] == "HOLD/ACCEPT"
    assert out["rows"][0]["cheap_exit"]["now"] is False
    import pathlib
    from tradepro_strategies.mcp import tools as T
    src = (pathlib.Path(T.__file__).parent / "server.py").read_text()
    assert '@instrumented("put_book")' in src
    monkeypatch.setattr(T, "_get", lambda path, **k: {"artifact": {"as_of_utc": "x", "put_book": {"counts": {}}}})
    assert T.put_book()["ok"] is True and T.put_book()["placed_orders"] == 0


def test_a_per_contract_exception_accepts_one_strike_and_rejects_the_rest():
    w = {"DDD": {"default": "no_more", "accept": ["2027-03-19:380"]}}
    assert flag_for("DDD", w, expiry="2027-03-19", strike=380.0) == "want"
    assert flag_for("DDD", w, expiry="2027-02-19", strike=420.0) == "no_more"
    assert flag_for("DDD", w) == "no_more"
    rows = [_row("DDD", 380, 6, 350, 110, expiry="2027-03-19"), _row("DDD", 420, 8, 350, 80, expiry="2027-02-19")]
    out = put_book(rows, positions=[], kv={"want_to_own": w, "cash_reserve_usd": 0, "cash_available_usd": 500_000}, nav_usd=5_000_000, today=date(2026, 11, 28))
    pol = {(r["expiry"], r["strike"]): r["policy"] for r in out["rows"]}
    assert pol[("2027-03-19", 380)] == "HOLD/ACCEPT" and pol[("2027-02-19", 420)] == "REPAIR"
