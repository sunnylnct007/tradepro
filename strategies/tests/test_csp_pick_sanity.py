"""External-review fixes (9 Aug 2026, owner-approved):

1. OI None-vs-0 honesty: a source that doesn't SERVE open interest must
   yield None (risk gate: "OI unavailable"), never a fabricated 0 that the
   gate reads as "illiquid". Verified live: our chain showed AAPL SEP26
   300P as OI 0 while true OI was ~21k — the None→0 coercion was the bug.
2. sane_csp_pick: a suggested CSP strike must be OTM with |delta| in
   0.15–0.40. Sparse/stale chains made nearest-to-target selection render a
   Δ0.92 put 13%% ITM (WMB) as a "suggestion" — a synthetic long, not a CSP.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli.options_screen import sane_csp_pick
from tradepro_strategies.quant_engine.options.risk import (
    MarketContext, PortfolioState, Regime, Structure, TradeCandidate, evaluate,
)


class TestSaneCspPick:
    def test_normal_otm_pick_passes(self):
        assert sane_csp_pick(abs_delta=0.27, strike=54.0, spot=57.5) is True

    def test_itm_put_rejected(self):
        # The WMB case: strike above spot = synthetic long, not a CSP.
        assert sane_csp_pick(abs_delta=0.92, strike=80.0, spot=70.7) is False

    def test_strike_at_spot_rejected(self):
        assert sane_csp_pick(abs_delta=0.35, strike=100.0, spot=100.0) is False

    @pytest.mark.parametrize("d", [0.06, 0.09, 0.13, 0.45, 0.60])
    def test_out_of_band_delta_rejected(self, d):
        assert sane_csp_pick(abs_delta=d, strike=90.0, spot=100.0) is False

    @pytest.mark.parametrize("d", [0.15, 0.27, 0.40])
    def test_band_edges_pass(self, d):
        assert sane_csp_pick(abs_delta=d, strike=90.0, spot=100.0) is True

    def test_missing_inputs_rejected(self):
        assert sane_csp_pick(None, 90.0, 100.0) is False
        assert sane_csp_pick(0.27, None, 100.0) is False
        assert sane_csp_pick(0.27, 90.0, None) is False


class TestOiNoneHonesty:
    def _cand(self):
        return TradeCandidate(symbol="AAPL", structure=Structure.CASH_SECURED_PUT,
                              abs_delta=0.27, dte=35, strike=300.0, notional_gbp=23000.0)

    def _ctx(self, oi):
        return MarketContext(
            regime=Regime.GREEN, falling_knife=False, iv_rank=45.0,
            open_interest=oi, bid_ask_spread_usd=0.05, premium_mid_usd=4.6,
            earnings_in_expiry_window=False, data_fresh=True)

    def test_oi_none_blocks_as_unavailable_not_illiquid(self):
        d = evaluate(self._cand(), self._ctx(None), PortfolioState())
        assert any("unavailable" in b and "interest" in b.lower() for b in d.blocks)
        assert not any("illiquid" in b for b in d.blocks)

    def test_real_zero_oi_still_blocks_as_illiquid(self):
        d = evaluate(self._cand(), self._ctx(0), PortfolioState())
        assert any("illiquid" in b for b in d.blocks)

    def test_g3_leg_without_oi_stays_none(self):
        from types import SimpleNamespace
        from tradepro_strategies.quant_engine.options import chains_g3

        def fake_get(url, **kw):
            if url.endswith("/months"):
                return SimpleNamespace(json=lambda: {"months": ["SEP26"], "error": None},
                                       raise_for_status=lambda: None)
            return SimpleNamespace(json=lambda: {
                "spot": 300.0, "error": None,
                "legs": [{"strike": 290.0, "right": "P", "bid": 4.5, "ask": 4.8,
                          "delta": -0.27, "impliedVolPct": 25.0, "openInterest": None}],
            }, raise_for_status=lambda: None)

        import pytest as _pytest
        mp = _pytest.MonkeyPatch()
        try:
            mp.setattr(chains_g3.requests, "get", fake_get)
            chain = chains_g3.fetch_chain_g3("AAPL", api_base="http://t", api_token="t")
        finally:
            mp.undo()
        assert chain is not None
        assert chain.puts[0].open_interest is None   # NOT a fabricated 0
