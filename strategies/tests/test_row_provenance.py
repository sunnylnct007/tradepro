"""Uniform provenance — the row must be able to say where every number came from.

Owner, 15 Aug 2026: "we shouldn't hit issues where we don't know if data is
coming from cache, yahoo or ibkr."

These tests assert on EMITTED VALUES from the real code paths, not on the
absence of something. That distinction is not pedantry: the 13 Aug late-bounce
test asserted `!= "BUY"` on a series that exited at an earlier guard, so it
passed for two days while the function it "covered" raised NameError on every
call. Each test below therefore names the exact string/grade it expects out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tradepro_strategies.ibkr_bars import bars_provenance
from tradepro_strategies.provenance import (
    ProvenanceBlock, describe, grade, source_label, age_text,
)

NOW = datetime(2026, 8, 15, 17, 0, tzinfo=timezone.utc)


def _frame(sources: list[str], last_ts: datetime) -> pd.DataFrame:
    idx = pd.date_range(end=last_ts, periods=len(sources), freq="D", tz="UTC")
    return pd.DataFrame({"close": [100.0] * len(sources), "source": sources}, index=idx)


# ── grading ──────────────────────────────────────────────────────────────

class TestGrade:
    def test_ibkr_is_golden(self):
        assert grade("ibkr_web") == "golden"
        assert grade("g3") == "golden"

    def test_yahoo_is_fallback_never_golden(self):
        assert grade("yfinance") == "fallback"
        assert grade("legacy_yahoo_cache") == "fallback"

    def test_solved_is_derived_not_fetched(self):
        assert grade("solved_only") == "derived"
        assert grade("cross_checked") == "derived"

    def test_unknown_source_grades_fallback_not_golden(self):
        # An unrecognised token must never be PROMOTED by accident.
        assert grade("some_new_provider") == "fallback"

    def test_missing_source_is_unavailable(self):
        assert grade(None) == "unavailable"

    def test_earnings_calendar_is_vendor_not_a_degradation(self):
        # If this graded `fallback`, every single-name row would read weak and
        # the summary would stop carrying information.
        assert grade("earnings_calendar") == "vendor"

    def test_labels_never_leak_code_words(self):
        assert source_label("ibkr_web") == "IBKR (Web API)"
        assert "fallback" in source_label("yfinance").lower()


class TestAgeText:
    def test_hours_and_days_are_distinguished(self):
        assert age_text(NOW - timedelta(hours=3), NOW) == "3.0h old"
        assert age_text(NOW - timedelta(days=4), NOW) == "4 days old"

    def test_minutes(self):
        assert age_text(NOW - timedelta(minutes=20), NOW) == "20m old"

    def test_none_stays_none(self):
        assert age_text(None, NOW) is None


# ── bars provenance: the actual "cache vs yahoo vs ibkr" answer ──────────

class TestBarsProvenance:
    def test_reports_the_bar_source_not_the_cache_hit(self):
        """`provider_used="cache"` is true and useless — the question is who
        ORIGINALLY produced the bars."""
        df = _frame(["ibkr_web"] * 10, NOW)
        p = bars_provenance(df, provider_used="cache", coverage_complete=True)
        assert p["source"] == "ibkr_web"
        assert p["rows"] == 10
        assert p["mixed"] is False

    def test_mixed_tail_is_flagged_and_names_the_last_bar_source(self):
        df = _frame(["yfinance"] * 5 + ["ibkr_web"] * 5, NOW)
        p = bars_provenance(df, provider_used="cache", coverage_complete=True)
        assert p["mixed"] is True
        assert p["source"] == "ibkr_web"
        assert set(p["sources"]) == {"yfinance", "ibkr_web"}

    def test_grades_only_the_recent_tail(self):
        """2024 bars from yahoo say nothing about today's close."""
        df = _frame(["yfinance"] * 300 + ["ibkr_web"] * 20, NOW)
        p = bars_provenance(df, provider_used="cache", coverage_complete=True)
        assert p["source"] == "ibkr_web"
        assert p["mixed"] is False, "old yahoo history must not taint a golden tail"

    def test_legacy_fallback_is_named_explicitly(self):
        df = pd.DataFrame({"close": [1.0]},
                          index=pd.date_range(end=NOW, periods=1, tz="UTC"))
        p = bars_provenance(df, legacy=True, error="store empty")
        assert p["source"] == "legacy_yahoo_cache"
        assert grade(p["source"]) == "fallback"

    def test_empty_frame_carries_the_error(self):
        p = bars_provenance(None, legacy=True, error="boom")
        assert p["source"] is None and p["rows"] == 0 and p["error"] == "boom"

    def test_as_of_is_the_last_bar(self):
        df = _frame(["ibkr_web"] * 3, NOW)
        p = bars_provenance(df, provider_used="ibkr_web", coverage_complete=True)
        assert p["as_of"].startswith("2026-08-15")


# ── the block: worst-grade + summary ─────────────────────────────────────

class TestProvenanceBlock:
    def _b(self, *pairs) -> ProvenanceBlock:
        return ProvenanceBlock(entries=[
            describe(input=k, label=k.title(), source=s, detail="d", now=NOW)
            for k, s in pairs])

    def test_worst_is_the_weakest_input(self):
        b = self._b(("bars", "ibkr_web"), ("premium", "yfinance"))
        assert b.worst == "fallback"

    def test_all_golden_says_so(self):
        b = self._b(("bars", "ibkr_web"), ("premium", "g3"))
        assert b.worst == "golden"
        assert b.summary == "All inputs from the golden source (IBKR)."

    def test_missing_input_dominates_a_fallback(self):
        b = self._b(("bars", "yfinance"), ("premium", None))
        assert b.worst == "unavailable"

    def test_summary_names_the_weak_inputs_only(self):
        b = self._b(("bars", "ibkr_web"), ("premium", "yfinance"))
        assert "Premium" in b.summary
        assert "Bars" not in b.summary, "a golden input must not clutter the warning"

    def test_derived_inputs_are_reported_not_warned_about(self):
        b = self._b(("bars", "ibkr_web"), ("iv", "solved_only"))
        assert b.worst == "derived"
        assert "computed by TradePro" in b.summary

    def test_to_dict_is_json_shaped(self):
        d = self._b(("bars", "ibkr_web")).to_dict()
        assert set(d) == {"worst", "summary", "inputs"}
        assert set(d["inputs"][0]) == {
            "input", "label", "source", "source_label", "trust", "detail",
            "as_of", "age"}


# ── the wheel row itself ─────────────────────────────────────────────────

@pytest.fixture
def row_provenance():
    from tradepro_strategies.cli.options_screen import row_provenance as rp
    return rp


class TestWheelRowProvenance:
    GOLDEN_BARS = {"source": "ibkr_web", "as_of": "2026-08-14T13:30:00+00:00",
                   "rows": 280, "mixed": False, "sources": ["ibkr_web"],
                   "coverage_complete": True, "error": None, "legacy": False}

    def _call(self, rp, **over):
        kw = dict(
            bars_prov=self.GOLDEN_BARS, spot_basis="daily_close",
            chain_source="g3", premium_source="live_mid",
            premium_as_of_utc="2026-08-15T16:00:00+00:00", premium=1.25,
            iv_solved={"iv": 0.29, "source": "cross_checked", "detail": "agree"},
            open_interest=4200, div_yield=0.031, div_yield_source="ibkr_web",
            is_etf=False, earnings_in_window=False, now=NOW)
        kw.update(over)
        return rp(**kw)

    def _entry(self, block, key):
        return next(e for e in block["inputs"] if e["input"] == key)

    def test_every_input_is_described_exactly_once(self, row_provenance):
        b = self._call(row_provenance)
        keys = [e["input"] for e in b["inputs"]]
        assert keys == ["bars", "spot", "premium", "iv", "open_interest",
                        "div_yield", "earnings"]

    def test_clean_single_name_row_has_no_weak_input(self, row_provenance):
        b = self._call(row_provenance)
        # `vendor` — the earnings calendar, the weakest link on a clean single
        # name, and correctly NOT a warning (no IBKR earnings feed exists).
        assert b["worst"] == "vendor"
        assert self._entry(b, "bars")["trust"] == "golden"
        assert self._entry(b, "premium")["source"] == "g3"
        assert b["summary"].startswith("All inputs from the golden source")

    def test_clean_etf_row_bottoms_out_at_derived(self, row_provenance):
        # No vendor calendar involved: bars/premium/OI golden, IV+earnings ours.
        b = self._call(row_provenance, is_etf=True)
        assert b["worst"] == "derived"

    def test_yahoo_bars_surface_as_fallback_with_the_age(self, row_provenance):
        """THE regression this whole workstream exists to prevent."""
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "source": "yfinance",
            "as_of": "2026-08-11T13:30:00+00:00"})
        bars = self._entry(b, "bars")
        assert bars["trust"] == "fallback"
        assert bars["source_label"] == "Yahoo (fallback)"
        assert bars["age"] == "4 days old"
        assert b["worst"] == "fallback"
        assert "Daily bars" in b["summary"]

    def test_legacy_cache_bars_are_loudly_labelled(self, row_provenance):
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "source": "legacy_yahoo_cache", "legacy": True})
        assert "LEGACY" in self._entry(b, "bars")["source_label"]

    def test_mixed_tail_is_downgraded_not_credited_to_the_last_bar(self, row_provenance):
        """LIVE case, 15 Aug 2026: XOM's us_equity partition holds 10 yfinance
        closes then 10 ibkr_web closes. The last bar is golden — but HV30, the
        Ichimoku regime and the 52w range all read ACROSS the window, so the
        row must not be graded on bar 20 alone."""
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "mixed": True,
            "sources": ["yfinance", "ibkr_web"],
            "tail_counts": {"yfinance": 10, "ibkr_web": 10}, "tail_n": 20})
        bars = self._entry(b, "bars")
        assert bars["trust"] == "fallback", "a half-yahoo tail is not golden"
        assert "yfinance×10" in bars["detail"]
        assert "10 of those 20 are NOT from the golden source" in bars["detail"]
        assert b["worst"] == "fallback"

    def test_mixed_tail_of_only_golden_sources_stays_golden(self, row_provenance):
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "mixed": True,
            "sources": ["ibkr", "ibkr_web"],
            "tail_counts": {"ibkr": 4, "ibkr_web": 16}, "tail_n": 20})
        assert self._entry(b, "bars")["trust"] == "golden"

    def test_spot_stays_golden_when_only_the_window_is_mixed(self, row_provenance):
        """The spot is the LAST close — genuinely IBKR — even when the window
        behind it is mixed. Downgrading it too would overstate the problem."""
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "mixed": True,
            "sources": ["yfinance", "ibkr_web"],
            "tail_counts": {"yfinance": 10, "ibkr_web": 10}, "tail_n": 20})
        assert self._entry(b, "spot")["trust"] == "golden"
        assert self._entry(b, "bars")["trust"] == "fallback"

    def test_a_real_hole_is_called_a_hole(self, row_provenance):
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "rows": 200, "rows_expected": 276,
            "missing_bars": 76, "coverage_complete": False})
        assert "GAPPY — 76 of 276 expected bars missing" in self._entry(b, "bars")["detail"]

    def test_one_missing_bar_does_not_cry_wolf(self, row_provenance):
        """LIVE case, 15 Aug 2026: XOM returns 275 bars against a 276 calendar
        ESTIMATE, so `coverage_complete` is False on a perfectly healthy
        history. Labelling that INCOMPLETE would scare-label every row — the
        exact cry-wolf failure the first data-readiness build shipped."""
        b = self._call(row_provenance, bars_prov={
            **self.GOLDEN_BARS, "rows": 275, "rows_expected": 276,
            "missing_bars": 1, "coverage_complete": False})
        d = self._entry(b, "bars")["detail"]
        assert "GAPPY" not in d and "INCOMPLETE" not in d
        assert "immaterial" in d
        assert self._entry(b, "bars")["trust"] == "golden"

    def test_no_bars_at_all_reports_the_error(self, row_provenance):
        b = self._call(row_provenance, bars_prov={
            "source": None, "as_of": None, "rows": 0, "error": "no daily bars"},
            spot_basis=None)
        assert self._entry(b, "bars")["trust"] == "unavailable"
        assert "no daily bars" in self._entry(b, "bars")["detail"]
        assert b["worst"] == "unavailable"

    def test_chain_spot_is_distinguished_from_a_settled_close(self, row_provenance):
        """A live/last-trade spot and an official close are different numbers.
        Conflating them mis-states how far OTM a strike really is."""
        b = self._call(row_provenance, spot_basis="chain_spot")
        spot = self._entry(b, "spot")
        assert spot["source"] == "g3"
        assert "NOT a settled close" in spot["detail"]

    def test_settled_close_spot_inherits_the_bar_source(self, row_provenance):
        spot = self._entry(self._call(row_provenance), "spot")
        assert spot["source"] == "ibkr_web"
        assert "settled daily close" in spot["detail"]

    def test_carried_premium_is_graded_carried_with_its_age(self, row_provenance):
        b = self._call(row_provenance, premium_source="carried_last_live",
                       premium_as_of_utc="2026-08-14T20:00:00+00:00")
        prem = self._entry(b, "premium")
        assert prem["trust"] == "carried"
        assert prem["age"] == "21.0h old"
        assert "hard-blocked" in prem["detail"]
        assert b["worst"] == "carried"

    def test_prev_close_premium_says_which_session(self, row_provenance):
        prem = self._entry(
            self._call(row_provenance, premium_source="prev_close_indicative"),
            "premium")
        assert "PRIOR SESSION" in prem["detail"]
        assert prem["trust"] == "golden"  # still IBKR-served, just not live

    def test_missing_premium_is_unavailable(self, row_provenance):
        prem = self._entry(self._call(row_provenance, premium=None), "premium")
        assert prem["trust"] == "unavailable"

    def test_solved_iv_is_derived_and_carries_the_solve_detail(self, row_provenance):
        b = self._call(row_provenance, iv_solved={
            "iv": 0.31, "source": "solved_only",
            "detail": "IV 31.0% SOLVED from the mid 1.25"})
        iv = self._entry(b, "iv")
        assert iv["trust"] == "derived"
        assert "SOLVED from the mid" in iv["detail"]

    def test_broker_iv_is_golden_but_flagged_unverified(self, row_provenance):
        iv = self._entry(self._call(row_provenance, iv_solved={
            "iv": 0.26, "source": "broker_only",
            "detail": "broker IV 26.0% (no mid to verify it against)"}), "iv")
        assert iv["trust"] == "golden"
        assert "unverified" in iv["source_label"]

    def test_iv_disagreement_stays_visible(self, row_provenance):
        iv = self._entry(self._call(row_provenance, iv_solved={
            "iv": 0.29, "source": "DISAGREEMENT", "detail": "DISAGREE by 14%"}), "iv")
        assert "DISAGREES" in iv["source_label"]

    def test_unavailable_iv_grades_unavailable(self, row_provenance):
        iv = self._entry(self._call(row_provenance, iv_solved={
            "iv": None, "source": "unavailable", "detail": "vega edge unknowable"}), "iv")
        assert iv["trust"] == "unavailable"

    def test_missing_oi_says_the_gate_cannot_be_evaluated(self, row_provenance):
        oi = self._entry(self._call(row_provenance, open_interest=None), "open_interest")
        assert oi["trust"] == "unavailable"
        assert "liquidity gate" in oi["detail"]

    def test_missing_div_yield_explains_the_forward_consequence(self, row_provenance):
        dy = self._entry(self._call(row_provenance, div_yield=None,
                                    div_yield_source="unavailable"), "div_yield")
        assert "rates-only" in dy["detail"]

    def test_div_yield_from_fundamentals_is_not_claimed_as_the_broker(self):
        """IBKR's field 7286 is dark across this universe (verified live 16 Aug:
        HTTP 200, 7283 "N/A", 7286 absent), so a present figure has usually come
        from OUR fundamentals. Reporting it as the broker snapshot would be the
        exact lie this block exists to prevent."""
        from tradepro_strategies.cli.options_screen import row_provenance as rp
        b = rp(bars_prov=TestWheelRowProvenance.GOLDEN_BARS, spot_basis="daily_close",
               chain_source="g3", premium_source="live_mid",
               premium_as_of_utc=None, premium=1.25,
               iv_solved={"iv": 0.29, "source": "cross_checked", "detail": "x"},
               open_interest=100, div_yield=0.0044, div_yield_source="fundamentals",
               is_etf=False, earnings_in_window=False, now=NOW)
        dy = next(e for e in b["inputs"] if e["input"] == "div_yield")
        assert dy["source"] == "fundamentals"
        assert "TradePro fundamentals" in dy["detail"]
        assert "field 7286 is dark" in dy["detail"]
        assert "broker snapshot" not in dy["detail"]

    def test_etf_earnings_is_structural_not_a_missing_lookup(self, row_provenance):
        """An ETF having no earnings is a fact of the security type — grading it
        `unavailable` would fabricate a data gap that does not exist."""
        e = self._entry(self._call(row_provenance, is_etf=True,
                                   earnings_in_window=False), "earnings")
        assert e["trust"] == "derived"
        assert "no earnings event exists" in e["detail"]

    def test_unverifiable_earnings_blocks_and_says_so(self, row_provenance):
        e = self._entry(self._call(row_provenance, earnings_in_window=None), "earnings")
        assert e["trust"] == "unavailable"
        assert "the gate blocks" in e["detail"]

    def test_single_name_earnings_is_vendor_graded(self, row_provenance):
        e = self._entry(self._call(row_provenance), "earnings")
        assert e["trust"] == "vendor"
        # ...and does not drag an otherwise-clean row into a warning.
        assert "Earnings" not in self._call(row_provenance)["summary"]


class TestRunLevelRollup:
    def test_fallback_bars_are_counted_and_degrade_the_run(self):
        """One yahoo row hides easily among 82; the run summary must count it."""
        from tradepro_strategies.cli.options_screen import screen_data_health

        def row(sym, src, trust):
            return {"symbol": sym, "vega_gate": "rank", "chain_source": "g3",
                    "suggested_premium": 1.0,
                    "provenance": {"worst": trust, "summary": "", "inputs": [
                        {"input": "bars", "source": src, "trust": trust,
                         "label": "Daily bars", "source_label": src,
                         "detail": "", "as_of": None, "age": None}]}}

        rows = ([row(f"S{i}", "ibkr_web", "golden") for i in range(9)]
                + [row("BAD", "yfinance", "fallback")])
        h = screen_data_health(rows, market_open=True)
        assert h["fallback_bar_count"] == 1
        assert h["bar_sources"]["ibkr_web"] == 9
        assert h["degraded"] is True
        assert "NOT priced off clean IBKR bars" in h["summary"]

    def test_a_mixed_history_row_is_counted_despite_an_ibkr_last_bar(self):
        """The rollup must count the GRADE. A mixed row reports source
        `ibkr_web` (its last bar) — counting tokens would hide it."""
        from tradepro_strategies.cli.options_screen import screen_data_health
        rows = [{"symbol": "XOM", "vega_gate": "rank", "chain_source": "g3",
                 "suggested_premium": 1.0,
                 "provenance": {"worst": "fallback", "summary": "", "inputs": [
                     {"input": "bars", "source": "ibkr_web", "trust": "fallback",
                      "label": "Daily bars", "source_label": "IBKR (Web API)",
                      "detail": "MIXED", "as_of": None, "age": None}]}}]
        h = screen_data_health(rows, market_open=True)
        assert h["fallback_bar_count"] == 1
        assert h["degraded"] is True

    def test_all_golden_run_is_not_degraded_by_provenance(self):
        from tradepro_strategies.cli.options_screen import screen_data_health

        rows = [{"symbol": f"S{i}", "vega_gate": "rank", "chain_source": "g3",
                 "suggested_premium": 1.0,
                 "provenance": {"worst": "golden", "summary": "", "inputs": [
                     {"input": "bars", "source": "ibkr_web", "trust": "golden",
                      "label": "Daily bars", "source_label": "IBKR",
                      "detail": "", "as_of": None, "age": None}]}}
                for i in range(10)]
        h = screen_data_health(rows, market_open=True)
        assert h["fallback_bar_count"] == 0
        assert h["degraded"] is False

    def test_rows_without_a_provenance_block_do_not_crash_the_rollup(self):
        from tradepro_strategies.cli.options_screen import screen_data_health
        rows = [{"symbol": "X", "vega_gate": "rank", "chain_source": "g3",
                 "suggested_premium": 1.0}]
        assert screen_data_health(rows, market_open=True)["fallback_bar_count"] == 0


class TestOpenInterestFallback:
    """OI is published ONCE A DAY by OCC, so our captured value for the same
    contract is the CURRENT figure, not a stale one. That is why it may be
    graded golden — and why the token must never be reused for prices.

    The gap it closes: the G3 chain served OI only patchily (16 Aug 2026 —
    KO 0 of 7 puts, SPY 9 of 19), leaving the liquidity gate unevaluable on
    59 of 82 rows. Those BLOCK correctly (a missing feed fails closed), so the
    board showed nothing because nobody could say whether the names were
    liquid — not because they were illiquid.
    """

    def test_live_chain_oi_is_preferred_and_labelled_g3(self):
        from tradepro_strategies.cli.options_screen import resolve_open_interest
        oi, src = resolve_open_interest("KO", expiry="2026-09-04", strike=82.0,
                                        right="P", chain_oi=1234)
        assert (oi, src) == (1234, "g3")

    def test_no_expiry_or_strike_cannot_be_matched(self):
        from tradepro_strategies.cli.options_screen import resolve_open_interest
        assert resolve_open_interest("KO", expiry=None, strike=82.0,
                                     right="P", chain_oi=None)[1] == "unavailable"
        assert resolve_open_interest("KO", expiry="2026-09-04", strike=None,
                                     right="P", chain_oi=None)[1] == "unavailable"

    def test_captured_oi_grades_golden_by_origin(self):
        """Verified live: every option_quote_daily row carries source='g3_chain',
        i.e. IBKR through our own chain feed."""
        assert grade("own_capture") == "golden"
        assert "own daily capture" in source_label("own_capture")

    def test_provenance_says_it_came_from_our_capture_not_the_live_chain(self):
        from tradepro_strategies.cli.options_screen import row_provenance as rp
        b = rp(bars_prov=TestWheelRowProvenance.GOLDEN_BARS, spot_basis="daily_close",
               chain_source="g3", premium_source="live_mid", premium_as_of_utc=None,
               premium=1.25, iv_solved={"iv": 0.29, "source": "cross_checked", "detail": "x"},
               open_interest=601, oi_source="own_capture", div_yield=0.02,
               div_yield_source="fundamentals", is_etf=False,
               earnings_in_window=False, now=NOW)
        e = next(x for x in b["inputs"] if x["input"] == "open_interest")
        assert e["source"] == "own_capture"
        assert "own capture" in e["detail"]
        assert "published once daily" in e["detail"]
