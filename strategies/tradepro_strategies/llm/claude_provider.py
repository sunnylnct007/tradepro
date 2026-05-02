"""Anthropic Claude HTTP provider.

Used for high-stakes prose generation where local 8B models fall short:
- Per-symbol decision rationales (Phase 6c)
- Earnings call / 10-K narrative analysis (Phase 6b, future)
- Sector / macro weekly digests (Phase 6e, future)

Configured via environment:
    TRADEPRO_LLM=claude
    ANTHROPIC_API_KEY=<your-key>
    TRADEPRO_CLAUDE_MODEL=<model id>   # default: claude-sonnet-4-5

Strict-output policy: every prompt the platform sends asks for JSON.
The provider parses the response defensively (substring extraction
fallback) so a malformed response degrades to ok=False rather than
raising — same contract as OllamaProvider.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from .provider import LlmProvider, LlmResult


class ClaudeProvider(LlmProvider):
    DEFAULT_MODEL = "claude-sonnet-4-5"   # latest Sonnet at time of writing

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._model = (
            model
            or os.environ.get("TRADEPRO_CLAUDE_MODEL")
            or self.DEFAULT_MODEL
        )
        self._timeout = timeout
        self._client = None

    @property
    def name(self) -> str: return "claude"

    @property
    def model(self) -> str: return self._model

    def healthy(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        try:
            from anthropic import Anthropic
        except ImportError:
            return None
        self._client = Anthropic(api_key=self._api_key, timeout=self._timeout)
        return self._client

    def complete_json(
        self,
        prompt: str,
        *,
        schema_hint: dict | None = None,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> LlmResult:
        client = self._ensure_client()
        if client is None:
            return LlmResult.fail("anthropic SDK unavailable or no API key")

        full_prompt = prompt
        if schema_hint is not None:
            full_prompt = (
                f"Respond with ONLY a JSON object matching this schema:\n"
                f"{json.dumps(schema_hint, indent=2)}\n\n"
                f"{prompt}\n\n"
                f"Output only the JSON object — no explanation, no code "
                f"fences, no preamble."
            )

        t0 = time.time()
        try:
            resp = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": full_prompt}],
            )
        except Exception as e:  # noqa: BLE001
            return LlmResult.fail(f"claude api: {e}")
        latency_ms = int((time.time() - t0) * 1000)

        # Anthropic returns content as a list of blocks. Concatenate the
        # text blocks; ignore tool-use / image blocks (we don't request them).
        try:
            blocks = resp.content
            raw_text = "".join(
                getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text"
            ).strip()
        except Exception as e:  # noqa: BLE001
            return LlmResult.fail(f"claude response parse: {e}")

        if not raw_text:
            return LlmResult.fail("claude returned empty content", raw=str(resp))

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            data = _extract_first_json(raw_text)
            if data is None:
                return LlmResult.fail(
                    "claude response wasn't valid JSON",
                    raw=raw_text[:500],
                )

        if not isinstance(data, dict):
            return LlmResult.fail(
                f"expected JSON object, got {type(data).__name__}",
                raw=raw_text[:500],
            )

        return LlmResult(
            ok=True,
            data=data,
            raw=raw_text,
            latency_ms=latency_ms,
            model=self._model,
        )


def _extract_first_json(text: str) -> dict | None:
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
