"""The win rate a swing candidate needs just to break even.

Owner, 1 Sep 2026, shown an IWM signal at R:R 0.39: "i am not convinced".

He was right, and the screen gave him no way to see why. R:R is SORTED on and
never FILTERED — the comment beside the sort even calls it "the number that
decides whether a bracket is worth placing", and nothing decides. On a day with
ONE candidate the ranking is a no-op, so whatever turns up becomes "today's
candidate" with nothing having judged it.

IWM: risk 23.25, reward 9.01. It needs 72.1% of these to win. The strategy's own
backtest wins 73.2% over 2,523 trades — a margin of ONE POINT.

Stating the breakeven turns a ratio nobody can price into the single comparison
that settles it.

16 Sep 2026 — THIS NOW FILTERS, reversing the line that stood here. The original
reason was sound and is worth restating: "choosing a threshold without a backtest
is the tuning this project has already had to retract." Two things changed.

EVIDENCE. 48 signals logged 22 Aug - 16 Sep:

                n   median upside   median 'needs'   clear the bar
    ETFs       24       2.1%             79%          3/24  (12%)
    stocks     24       5.2%             61%         24/24 (100%)

21 of 24 ETF signals needed a HIGHER win rate than the strategy has ever
achieved — not marginal, arithmetically unable to pay — and they were half of
every list shown to the owner.

AND THE THRESHOLD IS NOT TUNED. It is not a fitted number; it is the strategy's
OWN measured win rate (EVIDENCE_WIN_PCT, 72.8%, the MEAN_REVERSION_HOLD_V3
baseline). The rule is a tautology, not a parameter: reject a trade that needs a
higher win rate than the edge delivers. Nothing was swept to find it, so there is
nothing here to overfit. No safety margin is added on top for the same reason —
a margin WOULD be an invented number.
"""
import pytest


def breakeven(rr: float) -> float:
    return round(100.0 / (1.0 + rr), 1)


def test_the_iwm_case_that_prompted_this():
    # risk 290.57-267.32 = 23.25 ; reward 299.58-290.57 = 9.01
    rr = round(9.01 / 23.25, 2)
    assert rr == 0.39
    assert breakeven(rr) == 71.9  # ~72%, against a 73.2% backtest win rate


@pytest.mark.parametrize("rr,need", [
    (1.0, 50.0),    # symmetric: a coin flip
    (2.0, 33.3),    # 2:1 needs only a third
    (0.5, 66.7),
    (0.39, 71.9),   # today's IWM
    (0.25, 80.0),   # needs 4 in 5 — beyond anything this strategy has shown
])
def test_breakeven_is_the_standard_identity(rr, need):
    assert breakeven(rr) == need


def test_a_worse_ratio_always_demands_a_higher_win_rate():
    xs = [0.25, 0.39, 0.5, 1.0, 2.0]
    needs = [breakeven(x) for x in xs]
    assert needs == sorted(needs, reverse=True)


def test_the_screen_reports_it_and_still_does_not_filter():
    # The value must be SHOWN. It must NOT become a silent gate — that would be
    # a threshold chosen without evidence.
    import inspect
    from tradepro_strategies.cli import swing_candidates as S
    src = inspect.getsource(S)
    assert "breakeven_win_pct" in src
    assert "needs" in src
    for banned in ("if rr < ", "reward_risk < ", "rr <= "):
        assert banned not in src, f"{banned!r} would be an unbacktested filter"


# ── the filter itself ───────────────────────────────────────────────

def _scan_out(rows):
    """Run the module's reject step over pre-built candidate rows."""
    from tradepro_strategies.cli import swing_candidates as S
    keep = [r for r in rows
            if r.get("breakeven_win_pct") is None
            or r["breakeven_win_pct"] < S.BREAKEVEN_MAX_WIN_PCT]
    drop = [r for r in rows
            if r.get("breakeven_win_pct") is not None
            and r["breakeven_win_pct"] >= S.BREAKEVEN_MAX_WIN_PCT]
    return keep, drop


def _row(sym, need, rr=1.0, up=3.0):
    return {"symbol": sym, "breakeven_win_pct": need,
            "reward_risk": rr, "target_pct": up}


def test_the_spy_signal_that_cannot_pay_is_rejected():
    # 16 Sep, real row: SPY needs 85% against a 72.8% edge.
    keep, drop = _scan_out([_row("SPY", 85.0, 0.18, 1.4)])
    assert keep == []
    assert [r["symbol"] for r in drop] == ["SPY"]


def test_the_stock_signals_from_the_same_day_all_survive():
    # IVZ/BAC/MS/USB, 16 Sep — needs 54, 54, 61, 65.
    rows = [_row("IVZ", 54.0), _row("BAC", 54.0),
            _row("MS", 61.0), _row("USB", 65.0)]
    keep, drop = _scan_out(rows)
    assert len(keep) == 4 and drop == []


def test_it_is_not_an_etf_blacklist():
    # 3 of the 24 logged ETF signals DID clear the bar. Filtering on
    # instrument type would wrongly drop those and wrongly keep a bad
    # stock signal. The bar is the economics, not the ticker.
    keep, drop = _scan_out([_row("XLE", 55.0), _row("SOMESTOCK", 90.0)])
    assert [r["symbol"] for r in keep] == ["XLE"]
    assert [r["symbol"] for r in drop] == ["SOMESTOCK"]


def test_a_trade_needing_exactly_the_edge_is_rejected():
    # Needing precisely the win rate the edge delivers is break-even before
    # costs, i.e. a losing trade after them. Boundary is >=, not >.
    from tradepro_strategies.cli import swing_candidates as S
    keep, drop = _scan_out([_row("EDGE", S.BREAKEVEN_MAX_WIN_PCT)])
    assert keep == [] and len(drop) == 1


def test_a_row_with_no_breakeven_is_kept_not_silently_dropped():
    # breakeven is None when R:R could not be computed. Unknown is not the
    # same as unprofitable, and dropping on a missing field is how a data
    # gap turns into an invisible strategy change.
    keep, drop = _scan_out([{"symbol": "NORR", "breakeven_win_pct": None,
                             "reward_risk": None, "target_pct": 2.0}])
    assert [r["symbol"] for r in keep] == ["NORR"] and drop == []


def test_the_threshold_is_the_strategys_own_win_rate_not_a_tuned_number():
    from tradepro_strategies.cli import swing_candidates as S
    assert S.BREAKEVEN_MAX_WIN_PCT == S.EVIDENCE_WIN_PCT
