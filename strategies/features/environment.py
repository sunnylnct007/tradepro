"""Behave environment hooks — keep tests deterministic + fast.

Goal: every BDD scenario runs in ≤30s without hitting Yahoo or Ollama.
The comparator end-to-end run is intentionally NOT in the BDD suite —
it's a smoke test you run via `uv run tradepro-compare`. BDD tests
focus on the rule layer (bucket logic, sentiment demotion, schema
validation, rationale fallback) where determinism matters.
"""
from __future__ import annotations

import os


def before_all(context) -> None:
    # Force the LLM provider to NoOp so rationale tests don't fire
    # network calls. Tests that specifically exercise LLM paths set
    # this to ollama themselves via context.
    os.environ.setdefault("TRADEPRO_LLM", "noop")
    # Stable temp dir for any disk caches the modules might touch.
    context.tmp_root = os.environ.get("TRADEPRO_TEST_TMP", "/tmp/tradepro-tests")
    os.makedirs(context.tmp_root, exist_ok=True)


def before_scenario(context, scenario) -> None:
    # Reset any per-scenario state the steps will populate.
    context.facts = None
    context.row = None
    context.payload = None
    context.rationale = None
