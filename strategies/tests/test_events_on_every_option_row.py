"""Options review item 3 (28 Nov 2026): events inside every option's life on
the income rows, a clean earnings-move history, and both on MCP."""
from __future__ import annotations

from datetime import date

import pandas as pd


def test_income_rows_carry_events_and_warn_on_macro_inside_expiry(monkeypatch):
    import tradepro_strategies.income_spreads as I
    monkeypatch.setattr(I, "events_in_life", lambda base, sym, expiry: {
        "events": [{"kind": "fomc", "on": "2026-12-09", "title": "FOMC decision 9 Dec"},
                   {"kind": "earnings", "on": "2026-12-01", "title": f"{sym} earnings"}],
        "count": 2, "verified": True, "gaps": [], "names": "x"})
    # drive only the post-evaluate block: a minimal fake evaluate
    monkeypatch.setattr(I, "evaluate", lambda sym, **k: {"symbol": sym, "sector": "s", "bucket": "single",
                                                         "expiry": "2026-12-18", "rejected": [], "warnings": [], "gates": {}})
    monkeypatch.setattr(I, "_bars", lambda sym: pd.DataFrame({"close": [1.0] * 5}))
    monkeypatch.setattr(I, "technicals", lambda df: {"close": 100.0, "as_of": "2026-11-27"})
    monkeypatch.setattr(I, "_expiries", lambda b, t, s: ["2026-12-18"])
    monkeypatch.setattr(I, "pick_expiry", lambda exps, today, s: ("2026-12-18", "ok"))
    monkeypatch.setattr(I, "_earnings", lambda *a, **k: ("2027-02-01", "scheduled"))
    monkeypatch.setattr(I, "rank", lambda rows, s: rows)
    monkeypatch.setattr(I, "_live_chain", lambda *a, **k: ([], "n/a"))
    settings = dict(I.DEFAULTS); settings["single_name_seed"] = ["AAA"]; settings["sectors"] = {"AAA": "s"}
    monkeypatch.setattr(I, "settings", lambda b, t: settings)
    monkeypatch.setattr(I, "record", lambda b, t: [])
    monkeypatch.setattr(I, "_fx_gbp_per_usd", lambda b, t, s: (0.79, "fallback"))
    monkeypatch.setattr(I, "held_names", lambda b, t, rec: set())
    monkeypatch.setattr(I, "open_risk_gbp", lambda rec, fx: ({}, 0.0))
    monkeypatch.setattr(I, "open_csp_notional_gbp", lambda rec, fx: 0.0)
    out = I.scan("http://x", "t", only={"AAA"}, today=date(2026, 11, 28))
    rows = (out.get("candidates") or []) + (out.get("rejected") or [])
    row = next(r for r in rows if r["symbol"] == "AAA")
    assert row["events"]["count"] == 2
    assert any(w.startswith("macro_event_inside_expiry") and "FOMC" in w for w in row.get("warnings", []))


def test_earnings_moves_are_keyed_to_report_dates_only():
    from tradepro_strategies.events import earnings_moves
    idx = pd.date_range("2026-01-05", periods=12, freq="B")
    closes = [100, 100, 100, 110, 110, 110, 95, 95, 95, 95, 120, 120]   # a +10% print, a -14% shock, a +26% print
    bars = pd.DataFrame({"close": closes}, index=idx)
    prints = [idx[3].date(), idx[10].date()]            # the shock on idx[6] is NOT a print
    out = earnings_moves("AAA", api_base="http://x", bars=bars, report_dates=prints)
    assert out["n"] == 2 and out["verified"] is True
    assert out["moves_pct"] == [10.0, 26.32]
    assert out["median_pct"] == 18.16 and out["max_pct"] == 26.32


def test_earnings_moves_without_bars_is_a_gap_not_a_zero():
    from tradepro_strategies.events import earnings_moves
    out = earnings_moves("AAA", api_base="http://x", bars=pd.DataFrame(), report_dates=[date(2026, 1, 8)])
    assert out["verified"] is False and out["n"] == 0 and "no settled daily bars" in out["gaps"]


def test_both_are_on_mcp(monkeypatch):
    import pathlib
    from tradepro_strategies.mcp import tools as T
    src = (pathlib.Path(T.__file__).parent / "server.py").read_text()
    assert '@instrumented("events_for_contract")' in src and '@instrumented("earnings_moves")' in src
    import tradepro_strategies.events as E
    class _W:
        def to_dict(self): return {"events": [], "count": 0, "verified": True, "gaps": []}
    monkeypatch.setattr(E, "window_for_contract", lambda sym, exp, api_base: _W())
    assert T.events_for_contract("AAA", "2026-12-18")["ok"] is True
    monkeypatch.setattr(E, "earnings_moves", lambda sym, api_base, back_days: {"symbol": sym, "n": 0, "verified": False, "gaps": ["x"]})
    assert T.earnings_moves("AAA")["ok"] is True and T.earnings_moves("AAA")["verified"] is False
