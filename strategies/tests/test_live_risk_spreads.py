"""24 Nov 2026: the live book gained put credit spreads and a bought put-spread
hedge. A lane that counts every short put as a naked obligation overstates
defined-risk legs several times over. Long puts pair with short puts of the
same root and expiry. Synthetic book below."""
from __future__ import annotations

from datetime import date

from tradepro_strategies.cli.live_risk import build


def _pos(sym, qty, cost=None, mark=None, pnl=None):
    return {"symbol": sym, "assetCategory": "OPT" if len(sym) > 6 else "STK", "quantity": qty,
            "costBasisPrice": cost, "markPrice": mark, "unrealizedPnl": pnl, "reportDate": "2026-11-23"}


POSITIONS = [
    _pos("AAA   270115P00100000", -3, 2.0, 1.9, 30.0),    # short leg of a 100/90 credit spread
    _pos("AAA   270115P00090000", 3, 1.0, 0.9, -30.0),    # its long leg
    _pos("ETF   270115P00500000", 2, 20.0, 21.0, 200.0),   # bought hedge: long 500 ...
    _pos("ETF   270115P00450000", -2, 12.0, 11.5, 100.0),  # ... financed by the short 450
    _pos("BBB   261218P00200000", -1, 4.0, 30.0, -2600.0), # a naked put
]


def test_credit_spread_short_leg_counts_the_width_not_the_strike(monkeypatch):
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots", lambda syms: {s: None for s in syms})
    art = build(POSITIONS, today=date(2026, 11, 24))["artifact"]
    aaa = next(r for r in art["positions"] if r["symbol"] == "AAA")
    assert aaa["structure"] == "credit_spread" and aaa["long_strike"] == 90.0 and aaa["protected_qty"] == 3
    assert aaa["obligation_usd"] == 3000.0            # (100 − 90) × 100 × 3, not 30,000
    assert aaa["action"] == "HOLD" and "income monitor" in aaa["why"]
    etf = next(r for r in art["positions"] if r["symbol"] == "ETF")
    assert etf["structure"] == "hedge_short_leg" and etf["obligation_usd"] == 0.0
    bbb = next(r for r in art["positions"] if r["symbol"] == "BBB")
    assert bbb["structure"] == "naked" and bbb["obligation_usd"] == 20000.0
    assert art["assignment_obligation_usd"] == 23000.0   # 3,000 + 0 + 20,000
    assert art["counts"]["spreads"] == 1 and art["counts"]["hedge_legs"] == 1
    assert art["report_date"] == "2026-11-23"   # the session the Flex report describes


def test_an_unpaired_short_put_is_still_naked(monkeypatch):
    monkeypatch.setattr("tradepro_strategies.cli.live_risk._spots", lambda syms: {s: None for s in syms})
    art = build([_pos("AAA   270115P00100000", -3, 2.0, 1.9, 30.0),
                 _pos("AAA   270219P00090000", 3, 1.0, 0.9, -30.0)],  # different expiry: no pair
                today=date(2026, 11, 24))["artifact"]
    r = art["positions"][0]
    assert r["structure"] == "naked" and r["obligation_usd"] == 30000.0
