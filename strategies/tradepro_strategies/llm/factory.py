"""Provider selection — based on env config, with cheap-and-local default.

Resolution order (first match wins):
  1. TRADEPRO_LLM=noop                 → NoOpProvider (skip LLM entirely)
  2. TRADEPRO_LLM=ollama   (default)   → OllamaProvider, gated on health probe
  3. TRADEPRO_LLM=claude               → ClaudeProvider, gated on ANTHROPIC_API_KEY

If ollama is selected but the daemon isn't reachable, OR claude is
selected without an API key, we silently fall back to NoOp so the
comparator never crashes over a configuration gap. Sentiment / rationale
becomes 'no signal' instead, with the failure visible in the payload.

The `purpose` arg lets callers pick a different provider OR a different
MODEL per task — e.g. fast Ollama for sentiment scoring (lots of
headlines, simple structured output) but Claude for rationale
generation (fewer calls, prose quality matters). Override via:

    TRADEPRO_LLM_RATIONALE   — provider (ollama / claude / noop)
    TRADEPRO_LLM_SENTIMENT   — provider
    TRADEPRO_OLLAMA_MODEL_SENTIMENT — Ollama model for sentiment only
    TRADEPRO_OLLAMA_MODEL_RATIONALE — Ollama model for rationale only

Sentiment specifically defaults to llama3.1:8b regardless of the
global TRADEPRO_OLLAMA_MODEL — the audit (and lived experience)
flagged thinking models like qwen3.5 as unreliable for terse
structured-JSON output even with `think: false`. Pinning sentiment
to a non-thinking instruction model is the conservative default;
explicit override still works.
"""
from __future__ import annotations

import os

from .claude_provider import ClaudeProvider
from .ollama_provider import OllamaProvider
from .provider import LlmProvider, NoOpProvider


# Per-purpose Ollama model defaults. These win over the global
# TRADEPRO_OLLAMA_MODEL but lose to the per-purpose env override.
_PURPOSE_MODEL_DEFAULTS: dict[str, str] = {
    "sentiment": "llama3.1:8b",   # non-thinking; reliable terse JSON
    # rationale + verifier intentionally unset → fall through to global
}


def _resolve(choice: str, *, model: str | None = None) -> LlmProvider:
    choice = choice.strip().lower()
    if choice in ("noop", "none", ""):
        return NoOpProvider()
    if choice == "ollama":
        provider = OllamaProvider(model=model)
        return provider if provider.healthy() else NoOpProvider()
    if choice == "claude":
        provider = ClaudeProvider()
        return provider if provider.healthy() else NoOpProvider()
    return NoOpProvider()


def get_provider(purpose: str | None = None) -> LlmProvider:
    """Return the LLM provider for a given purpose.

    `purpose` is a free-form tag. Today's purposes:

        sentiment    — per-headline scoring (Ollama llama3.1:8b default)
        rationale    — per-symbol prose summary (global default)
        verifier     — answer verification (global default)

    No `purpose` → use the global default (TRADEPRO_LLM env, fallback ollama).
    """
    if purpose:
        # Per-purpose provider override.
        scoped = os.environ.get(f"TRADEPRO_LLM_{purpose.upper()}")
        # Per-purpose model: env wins, then the hard-coded default below,
        # then None (which means OllamaProvider falls back to the global
        # TRADEPRO_OLLAMA_MODEL or its DEFAULT_MODEL).
        scoped_model = (
            os.environ.get(f"TRADEPRO_OLLAMA_MODEL_{purpose.upper()}")
            or _PURPOSE_MODEL_DEFAULTS.get(purpose.lower())
        )
        if scoped:
            return _resolve(scoped, model=scoped_model)
        if scoped_model:
            # No provider override but a model override exists — apply
            # it on top of the global provider choice.
            global_choice = os.environ.get("TRADEPRO_LLM") or "ollama"
            return _resolve(global_choice, model=scoped_model)
    return _resolve(os.environ.get("TRADEPRO_LLM") or "ollama")
