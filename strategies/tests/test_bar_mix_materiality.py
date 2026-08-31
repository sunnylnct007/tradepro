"""A mixed-provider bar tail downgrades the row only when the mix is MATERIAL.

THE DEFECT, 31 Aug 2026. The wheel board showed an amber "FALLBACK bars, spot,
premium, div" badge on nearly every row. Measured on the live screen:

    rows whose BARS were graded fallback : 73 of 82
    rows whose WORST grade was fallback  : 77 of 82

Almost always from ONE yfinance close in a twenty-bar tail. A badge that fires
on 94% of the board discriminates nothing — the exact failure `ProvenanceCell`
already records for 17 Aug, when all 82 rows read "MISSING" and the column cost
a table width to say so.

And the contamination was not distorting anything. Checked against the series:

    GDX  2026-08-27  103.69  +0.91%  yfinance   (between 102.76 and 99.65)
    PLTR 2026-08-26  171.56  -0.68%  yfinance   (the +8.38% jump beside it is ibkr_web)

One agreeing close does not move HV30 or the Ichimoku regime.

The strict rule existed for a real case — XOM on 15 Aug, TEN of the last twenty
closes from yfinance, with HV30, the regime and the 52-week range all reading
across them. That case must still grade fallback. This is the same materiality
treatment the function already applies to missing bars a few lines below, where
a single market holiday must not put a scare label on a healthy 275-bar history.

The count stays in the `detail` string either way, so nothing is hidden. Only
the row's TRUST GRADE stops moving on an immaterial mix.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli.options_screen import row_provenance


def _bp(counts: dict, n: int = 20, rows: int = 275):
    """A bar-provenance block as the screen builds it."""
    return {
        "source": "ibkr_web", "rows": rows, "as_of": "2026-08-28",
        "mixed": len(counts) > 1, "tail_counts": counts, "tail_n": n,
        "missing_bars": 0, "rows_expected": rows,
    }


def _bars_entry(bp):
    prov = row_provenance(
        bars_prov=bp, spot_basis=None, chain_source="g3",
        premium_source="g3", oi_source="g3",
        premium_as_of_utc=None, premium=1.40,
        iv_solved=None, open_interest=500,
        div_yield=None, div_yield_source=None, is_etf=False,
        earnings_in_window=False)
    return next(i for i in prov["inputs"] if i["input"] == "bars")


@pytest.mark.parametrize("weak", [1, 2])
def test_an_immaterial_mix_does_not_downgrade_the_row(weak):
    """THE regression: 1-2 yfinance closes in twenty graded the whole row
    fallback, on 73 of 82 rows."""
    e = _bars_entry(_bp({"ibkr_web": 20 - weak, "yfinance": weak}))
    assert e["trust"] != "fallback", e
    # Still SAID, just not used to downgrade — nothing is hidden.
    assert f"yfinance×{weak}" in e["detail"], e["detail"]
    assert "immaterial" in e["detail"], e["detail"]


def test_a_material_mix_still_downgrades():
    """XOM, 15 Aug: ten of twenty from yfinance, with HV30/regime/52w all
    reading across them. This is what the rule is for."""
    e = _bars_entry(_bp({"ibkr_web": 10, "yfinance": 10}))
    assert e["trust"] == "fallback", e
    assert "NOT from the golden source" in e["detail"]


def test_the_threshold_needs_both_count_and_share():
    """3 of 20 is 15% and three bars — material. 3 of 60 is 5% — not."""
    assert _bars_entry(_bp({"ibkr_web": 17, "yfinance": 3}, n=20))["trust"] == "fallback"
    assert _bars_entry(_bp({"ibkr_web": 57, "yfinance": 3}, n=60))["trust"] != "fallback"


def test_an_all_golden_tail_is_untouched():
    e = _bars_entry(_bp({"ibkr_web": 20}))
    assert e["trust"] != "fallback"
    assert "MIXED" not in e["detail"]
