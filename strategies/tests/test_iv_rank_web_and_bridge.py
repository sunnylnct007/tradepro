"""OAuth-only IV-Rank path (9 Aug 2026, owner decision: no local Gateway).

Covers:
  * `_pct_to_frac` — IBKR snapshot vol fields arrive as display strings
    ("25.381%"); field 7282 is AVERAGE VOLUME ("8.98M"), never IV rank.
  * `fetch_iv_rank_web` — warm-up retry, dataset upsert, rank-vs-bridge mode
    selection, fail-closed when the snapshot serves no IV.
  * the risk engine's two-tier vega gate — rank when honest, IV/HV bridge
    (loud warning) while the dataset accumulates, BLOCK when neither exists.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradepro_strategies.quant_engine.options.iv_rank import (
    _pct_to_frac, fetch_iv_rank_web,
)
from tradepro_strategies.quant_engine.options.risk import (
    MarketContext, OptionsRiskConfig, PortfolioState, Regime, Structure,
    TradeCandidate, evaluate,
)


class TestPctToFrac:
    def test_percent_display_string(self):
        assert _pct_to_frac("25.381%") == pytest.approx(0.25381)

    def test_bare_fraction_passes_through(self):
        assert _pct_to_frac(0.19) == pytest.approx(0.19)

    def test_bare_percent_number_scales(self):
        # A number like 24.6 (>3) can only be a percent display — scale it.
        assert _pct_to_frac(24.6) == pytest.approx(0.246)

    @pytest.mark.parametrize("garbage", [None, "", "n/a", "8.98M", "-5%", 0, "0%"])
    def test_garbage_returns_none(self, garbage):
        # "8.98M" is field 7282 = average volume — must never parse as vol.
        assert _pct_to_frac(garbage) is None


class _FakeHttp:
    """Scripted GET/POST double for fetch_iv_rank_web (conftest blocks real
    requests). `quote_snaps` is consumed per GET on the quote URL — lets tests
    exercise the warm-up retry."""

    def __init__(self, quote_snaps, history_series=None, fail_post=False):
        self.quote_snaps = list(quote_snaps)
        self.history_series = history_series if history_series is not None else []
        self.fail_post = fail_post
        self.posted = []

    def get(self, url, headers=None, timeout=None):
        if "/quote" in url:
            snap = self.quote_snaps.pop(0) if self.quote_snaps else {}
            return SimpleNamespace(json=lambda s=snap: {"snapshot": s})
        if "/iv-daily/" in url:
            series = [{"iv": v} for v in self.history_series]
            return SimpleNamespace(json=lambda: {"series": series})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, headers=None, timeout=None):
        if self.fail_post:
            raise ConnectionError("iv-daily down")
        self.posted.append((url, json))
        return SimpleNamespace(json=lambda: {"ok": True})


def _fetch(http, **kw):
    return fetch_iv_rank_web(
        "CVX", api_base="http://test", api_token="t",
        _http=http, _sleep=lambda s: None, **kw)


class TestFetchIvRankWeb:
    def test_warmup_retry_then_bridge_mode(self):
        http = _FakeHttp(
            quote_snaps=[{}, {"7283": "25.381%", "7631": "20.0%"}],  # cold then warm
            history_series=[0.24, 0.25],                              # 2d — shallow
        )
        r = _fetch(http)
        assert r.available is True
        assert r.iv == pytest.approx(0.25381)
        assert r.iv_rank is None                     # window too shallow for a rank
        assert r.iv_hv_ratio == pytest.approx(1.269, abs=1e-3)
        assert "bridge" in r.reason
        assert len(http.posted) == 1                 # today's row went to the dataset

    def test_rank_mode_when_window_deep_enough(self):
        series = [0.20 + i * 0.001 for i in range(80)]  # 80d accumulated
        http = _FakeHttp(
            quote_snaps=[{"7283": "28.0%", "7631": "20.0%"}],
            history_series=series,
        )
        r = _fetch(http, min_window_days=60)
        assert r.available is True
        assert r.iv_rank is not None
        assert r.days == 80
        assert "own 80d dataset" in r.reason

    def test_no_iv_after_retries_fails_closed(self):
        http = _FakeHttp(quote_snaps=[{}, {}, {}])
        r = _fetch(http)
        assert r.available is False
        assert "no IV" in r.reason

    def test_failed_dataset_upsert_does_not_kill_the_fetch(self):
        http = _FakeHttp(
            quote_snaps=[{"7283": "25.0%", "7631": "22.0%"}],
            fail_post=True,
        )
        r = _fetch(http)
        assert r.available is True
        assert r.iv_hv_ratio == pytest.approx(1.136, abs=1e-3)


def _cand():
    return TradeCandidate(
        symbol="CVX", structure=Structure.CASH_SECURED_PUT,
        abs_delta=0.27, dte=35, strike=100.0, notional_gbp=8000.0)


def _ctx(**kw):
    base = dict(
        regime=Regime.GREEN, falling_knife=False, pct_off_52w_high=25.0,
        open_interest=500, bid_ask_spread_usd=0.05, premium_mid_usd=1.5,
        earnings_in_expiry_window=False, data_fresh=True)
    base.update(kw)
    return MarketContext(**base)


class TestVegaGateTwoTier:
    def test_rank_gate_still_primary(self):
        d = evaluate(_cand(), _ctx(iv_rank=45.0), PortfolioState())
        assert d.allowed is True

    def test_bridge_passes_with_loud_warning(self):
        d = evaluate(_cand(), _ctx(iv_rank=None, iv_hv_ratio=1.25,
                                   iv_rank_window_days=12), PortfolioState())
        assert d.allowed is True
        assert any("IV/HV bridge" in w for w in d.warnings)
        assert any("12d" in w for w in d.warnings)

    def test_bridge_blocks_thin_premium(self):
        d = evaluate(_cand(), _ctx(iv_rank=None, iv_hv_ratio=0.85,
                                   iv_rank_window_days=12), PortfolioState())
        assert d.allowed is False
        assert any("IV/HV 0.85" in b for b in d.blocks)

    def test_neither_metric_still_blocks(self):
        d = evaluate(_cand(), _ctx(iv_rank=None, iv_hv_ratio=None), PortfolioState())
        assert d.allowed is False
        assert any("IV-Rank unavailable" in b for b in d.blocks)

    def test_rank_present_ignores_bridge(self):
        # A poor rank must block even if the bridge ratio looks rich.
        d = evaluate(_cand(), _ctx(iv_rank=10.0, iv_hv_ratio=2.0), PortfolioState())
        assert d.allowed is False
        assert any("IV-Rank 10%" in b for b in d.blocks)


class TestPremiumFloor:
    """Owner 2026-08-09: 'avoid selling options not paying much' — a clean
    candidate that only pays pennies must be refused."""

    def test_missing_premium_blocks(self):
        d = evaluate(_cand(), _ctx(iv_rank=45.0, premium_mid_usd=None), PortfolioState())
        assert d.allowed is False
        assert any("Premium (mid) unavailable" in b for b in d.blocks)

    def test_penny_premium_blocks(self):
        d = evaluate(_cand(), _ctx(iv_rank=45.0, premium_mid_usd=0.10), PortfolioState())
        assert d.allowed is False
        assert any("pennies" in b for b in d.blocks)

    def test_thin_annualised_yield_blocks(self):
        # $0.40 on a $100 strike over 35d ≈ 4.2%/yr — under the bank-beating floor.
        d = evaluate(_cand(), _ctx(iv_rank=45.0, premium_mid_usd=0.40), PortfolioState())
        assert d.allowed is False
        assert any("Annualised yield 4.2%" in b for b in d.blocks)

    def test_rich_premium_passes(self):
        # $1.50 on $100 over 35d ≈ 15.6%/yr — clears both floors.
        d = evaluate(_cand(), _ctx(iv_rank=45.0, premium_mid_usd=1.50), PortfolioState())
        assert d.allowed is True


class TestDivYieldParse:
    def test_percent_string_parses(self):
        from tradepro_strategies.quant_engine.options.iv_rank import _div_yield_frac
        assert _div_yield_frac("3.4%") == pytest.approx(0.034)

    @pytest.mark.parametrize("bad", [None, 2.72, "2.72", "", "n/a", "40%"])
    def test_ambiguous_or_garbage_returns_none(self, bad):
        # A bare number may be dividend AMOUNT per share — never guess a yield.
        from tradepro_strategies.quant_engine.options.iv_rank import _div_yield_frac
        assert _div_yield_frac(bad) is None
