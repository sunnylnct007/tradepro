"""Guardrail tests for the LLM catalyst judge.

These pin the *post-processing* logic deterministically with a fake provider —
the part that protects the live verdict from weak-model noise (BULLISH with no
named event, strength-0 directional calls, garbage verdicts). The real LLM is
non-deterministic and not exercised here.
"""
from __future__ import annotations

from tradepro_strategies.catalyst_llm import judge_catalyst
from tradepro_strategies.llm.provider import LlmResult


class _FakeProvider:
    """Returns a canned LlmResult so we can assert the guardrails."""

    def __init__(self, data: dict, ok: bool = True):
        self._data = data
        self._ok = ok

    @property
    def healthy(self) -> bool:
        return True

    def complete_json(self, prompt, *, schema_hint=None, max_tokens=256, temperature=0.0):
        return LlmResult(ok=self._ok, data=self._data) if self._ok else LlmResult.fail("boom")


_NEWS = [{"title": "Something happened to the company", "sentiment": 0.1}]


def test_grounded_bullish_passes_through():
    p = _FakeProvider({"event": "FDA approval", "verdict": "BULLISH", "strength": 2, "reason": "approved"})
    r = judge_catalyst("AAA", _NEWS, provider=p, use_cache=False)
    assert r["verdict"] == "BULLISH" and r["strength"] == 2 and r["event"] == "FDA approval"


def test_directional_with_no_event_forced_neutral():
    # The exact weak-model failure: a direction with no real event.
    p = _FakeProvider({"event": "none", "verdict": "BULLISH", "strength": 1, "reason": "vibes"})
    r = judge_catalyst("AAA", _NEWS, provider=p, use_cache=False)
    assert r["verdict"] == "NEUTRAL" and r["strength"] == 0


def test_no_event_marker_phrases_forced_neutral():
    p = _FakeProvider({"event": "no specific event", "verdict": "BEARISH", "strength": 2, "reason": "x"})
    r = judge_catalyst("AAA", _NEWS, provider=p, use_cache=False)
    assert r["verdict"] == "NEUTRAL"


def test_bullish_strength_zero_clamped_up():
    # A grounded direction must be at least moderate so it can't slip in as a no-op.
    p = _FakeProvider({"event": "IPO debut", "verdict": "BULLISH", "strength": 0, "reason": "y"})
    r = judge_catalyst("AAA", _NEWS, provider=p, use_cache=False)
    assert r["verdict"] == "BULLISH" and r["strength"] == 1


def test_garbage_verdict_becomes_neutral():
    p = _FakeProvider({"event": "merger", "verdict": "MOON", "strength": 2, "reason": "z"})
    r = judge_catalyst("AAA", _NEWS, provider=p, use_cache=False)
    assert r["verdict"] == "NEUTRAL"


def test_no_news_is_neutral_without_calling_llm():
    r = judge_catalyst("AAA", [], use_cache=False)
    assert r["verdict"] == "NEUTRAL" and r["strength"] == 0


def test_llm_failure_degrades_to_neutral():
    p = _FakeProvider({}, ok=False)
    r = judge_catalyst("AAA", _NEWS, provider=p, use_cache=False)
    assert r["verdict"] == "NEUTRAL"
