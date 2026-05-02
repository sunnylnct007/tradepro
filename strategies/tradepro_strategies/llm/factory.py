"""Provider selection — based on env config, with cheap-and-local default.

Resolution order (first match wins):
  1. TRADEPRO_LLM=noop                 → NoOpProvider (skip LLM entirely)
  2. TRADEPRO_LLM=ollama   (default)   → OllamaProvider, gated on health probe
  3. TRADEPRO_LLM=claude               → ClaudeProvider, gated on ANTHROPIC_API_KEY

If ollama is selected but the daemon isn't reachable, OR claude is
selected without an API key, we silently fall back to NoOp so the
comparator never crashes over a configuration gap. Sentiment / rationale
becomes 'no signal' instead, with the failure visible in the payload.

The `purpose` arg lets callers pick a different provider per task —
e.g. fast Ollama for sentiment scoring (lots of headlines) but Claude
for rationale generation (fewer calls, prose quality matters). Override
via TRADEPRO_LLM_RATIONALE / TRADEPRO_LLM_SENTIMENT etc.
"""
from __future__ import annotations

import os

from .claude_provider import ClaudeProvider
from .ollama_provider import OllamaProvider
from .provider import LlmProvider, NoOpProvider


def _resolve(choice: str) -> LlmProvider:
    choice = choice.strip().lower()
    if choice in ("noop", "none", ""):
        return NoOpProvider()
    if choice == "ollama":
        provider = OllamaProvider()
        return provider if provider.healthy() else NoOpProvider()
    if choice == "claude":
        provider = ClaudeProvider()
        return provider if provider.healthy() else NoOpProvider()
    return NoOpProvider()


def get_provider(purpose: str | None = None) -> LlmProvider:
    """Return the LLM provider for a given purpose.

    `purpose` is a free-form tag — if you set TRADEPRO_LLM_<PURPOSE>=
    claude in the environment, that purpose uses Claude even when the
    global default is Ollama. Today's purposes:

        sentiment    — per-headline scoring (OllamaProvider preferred)
        rationale    — per-symbol prose summary (Claude preferred when keyed)
        verifier     — answer verification (Ollama is fine)

    No `purpose` → use the global default (TRADEPRO_LLM env, fallback ollama).
    """
    if purpose:
        scoped = os.environ.get(f"TRADEPRO_LLM_{purpose.upper()}")
        if scoped:
            return _resolve(scoped)
    return _resolve(os.environ.get("TRADEPRO_LLM") or "ollama")
