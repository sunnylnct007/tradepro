"""Plain-English rationale per symbol — the readable companion to the
structured decision_trace."""
from __future__ import annotations

from typing import Literal

from ._base import TPModel


class Rationale(TPModel):
    summary: str
    key_factors: list[str] = []
    caveats: list[str] = []
    # source distinguishes LLM-generated (verified) from deterministic
    # template fallbacks. Always shown in the UI so the user knows
    # whether the prose was AI-written or built mechanically from facts.
    source: Literal[
        "llm",
        "template",
        "template_no_llm",
        "template_llm_failed",
        "template_empty_llm",
        "template_llm_unverified",
    ] = "template"
    model: str | None = None
    prompt_version: str = "v1"
    verified: bool = False
    verification_notes: list[str] = []
    generated_at: str | None = None
