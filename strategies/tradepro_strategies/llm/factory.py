"""Provider selection — based on env config, with cheap-and-local default.

Resolution order (first match wins):
  1. TRADEPRO_LLM=noop                 → NoOpProvider (skip LLM entirely)
  2. TRADEPRO_LLM=ollama   (default)   → OllamaProvider, gated on health probe
  3. TRADEPRO_LLM=claude               → reserved (Phase 6b); falls back to noop today

If ollama is selected but the daemon isn't reachable, we silently fall
back to NoOp so a missing local Ollama doesn't break the comparator
run — sentiment becomes 'no signal' rather than crashing.
"""
from __future__ import annotations

import os

from .ollama_provider import OllamaProvider
from .provider import LlmProvider, NoOpProvider


def get_provider() -> LlmProvider:
    choice = (os.environ.get("TRADEPRO_LLM") or "ollama").strip().lower()

    if choice == "noop" or choice == "none":
        return NoOpProvider()

    if choice == "ollama":
        provider = OllamaProvider()
        if provider.healthy():
            return provider
        # Daemon down → degrade gracefully.
        return NoOpProvider()

    if choice == "claude":
        # Reserved for Phase 6b. For now: not implemented, fall back.
        return NoOpProvider()

    return NoOpProvider()
