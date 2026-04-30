"""LLM provider abstraction.

The LLM is treated as an external service even when it runs in-process
or on the same Mac. The abstraction (LlmProvider) lets us swap
implementations without touching callers — Ollama for free local work,
Claude API for nuanced analysis, NoOp for graceful degradation when
neither is configured.

Strict principle: the LLM produces context and explanations, NEVER
the verdict. Outputs feed into the rule-based decision_trace as
additional checks; they don't override the rule chain.
"""
from .factory import get_provider
from .provider import LlmProvider, LlmResult, NoOpProvider

__all__ = ["LlmProvider", "LlmResult", "NoOpProvider", "get_provider"]
