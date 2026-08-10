"""Verdict funnel (owner 10 Aug 2026: "0 BUY — how is that even possible").

compare.py attaches verdict_funnel {technical, consensus, final, demotions}
per row; the digest aggregates it into one line so "market judgment" and
"data problem" can never again produce byte-identical 0-BUY output.
"""
from __future__ import annotations

from tradepro_strategies.email_digest import _verdict_funnel_summary


def _payload(rows):
    return [{"payload": {"rows": rows}}]


def _row(sym, technical="BUY", consensus="BUY", final="WAIT", demotions=None):
    return {"symbol": sym, "bucket": final,
            "verdict_funnel": {"technical": technical, "consensus": consensus,
                               "final": final, "demotions": demotions or []}}


class TestVerdictFunnelSummary:
    def test_no_funnel_data_returns_none(self):
        rows = [{"symbol": "AAPL", "bucket": "WAIT"}]
        assert _verdict_funnel_summary(_payload(rows)) is None

    def test_counts_survivors_and_causes(self):
        rows = [
            _row("AAPL", final="BUY"),
            _row("MSFT", demotions=["sentiment"]),
            _row("NVDA", demotions=["earnings_veto:EARNINGS_RECENT_NEWS"]),
            _row("GOOG", demotions=["llm_data_gap"]),
            _row("KO", technical="HOLD", consensus="WAIT", final="WAIT"),  # never a BUY — not in funnel
        ]
        s = _verdict_funnel_summary(_payload(rows))
        assert s is not None
        assert "4 technical BUY → 1 survived" in s
        assert "news sentiment" in s
        assert "earnings window (EARNINGS_RECENT_NEWS)" in s
        assert "LLM verification gap (DATA)" in s

    def test_consensus_demotion_without_named_cause(self):
        # technical BUY, consensus already WAIT (strategy votes) — attributed
        # to the consensus vote, not left invisible.
        rows = [_row("XOM", technical="BUY", consensus="WAIT", final="WAIT")]
        s = _verdict_funnel_summary(_payload(rows))
        assert s is not None and "strategy consensus" in s

    def test_symbol_deduped_across_strategy_rows(self):
        rows = [_row("AAPL", final="BUY"), _row("AAPL", final="BUY")]
        s = _verdict_funnel_summary(_payload(rows))
        assert "1 technical BUY → 1 survived" in s

    def test_no_technical_buys_returns_none(self):
        rows = [_row("KO", technical="HOLD", consensus="WAIT", final="WAIT")]
        assert _verdict_funnel_summary(_payload(rows)) is None
