"""Ollama embedder. Local, free, runs on Apple-silicon MPS.

Default model: `mxbai-embed-large` (1024-dim, ~700 MB, very strong
on financial / technical text). Override with TRADEPRO_EMBED_MODEL.

Failure modes are visible, not silent:
- Ollama down → `OllamaEmbedder.healthy()` returns False, callers
  decide whether to fall through (usually skip embedding for that
  pass and try again next refresh).
- Model not pulled → /api/embeddings returns 404; we surface the
  exact error message the user can act on (e.g. "ollama pull
  mxbai-embed-large").
"""
from __future__ import annotations

import os
import time

import numpy as np
import requests

DEFAULT_MODEL = "mxbai-embed-large"


class OllamaEmbedder:
    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        timeout: float = 30.0,
    ):
        self._model = (
            model or os.environ.get("TRADEPRO_EMBED_MODEL") or DEFAULT_MODEL
        )
        self._host = (
            host or os.environ.get("TRADEPRO_OLLAMA_HOST")
            or "http://localhost:11434"
        ).rstrip("/")
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    def healthy(self) -> bool:
        """Cheap probe — daemon up + model loaded. We don't pre-load;
        Ollama lazy-loads on first request."""
        try:
            r = requests.get(f"{self._host}/api/tags", timeout=2)
            if r.status_code != 200:
                return False
            tags = (r.json() or {}).get("models", [])
            # Loose match on model name (Ollama tags can be 'name' or
            # 'name:tag' — accept either).
            return any(self._model in (t.get("name") or "") for t in tags)
        except requests.RequestException:
            return False

    def embed(self, text: str) -> np.ndarray | None:
        """Embed one string. Returns None on failure (network /
        non-200 / parse), with the error visible via embed_with_meta."""
        result = self.embed_with_meta(text)
        return result.get("vector") if result else None

    def embed_with_meta(self, text: str) -> dict:
        """Returns {ok, vector, model, latency_ms, error}. Used by the
        embedding-store updater so failures land in the run log
        instead of being swallowed."""
        if not text or not text.strip():
            return {"ok": False, "error": "empty text"}
        body = {"model": self._model, "prompt": text}
        t0 = time.time()
        try:
            r = requests.post(
                f"{self._host}/api/embeddings",
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as e:
            return {"ok": False, "error": f"network: {e}"}
        latency_ms = int((time.time() - t0) * 1000)

        if r.status_code != 200:
            err = r.text[:200]
            if "model not found" in err.lower() or r.status_code == 404:
                err = (
                    f"model '{self._model}' not pulled — run: "
                    f"ollama pull {self._model}"
                )
            return {"ok": False, "error": f"http {r.status_code}: {err}"}

        try:
            payload = r.json()
            vec = payload.get("embedding") or payload.get("embeddings")
            if vec is None or not isinstance(vec, list) or not vec:
                return {"ok": False, "error": "no embedding in response"}
            arr = np.asarray(vec, dtype=np.float32)
            return {
                "ok": True,
                "vector": arr,
                "model": self._model,
                "latency_ms": latency_ms,
                "dim": int(arr.shape[0]),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"parse: {e}"}


def embedder_for(_purpose: str = "default") -> OllamaEmbedder:
    """Factory mirror of llm.get_provider() — kept simple for now;
    add ClaudeEmbedder / NoOp etc. when needed."""
    return OllamaEmbedder()


def embed_query(query: str) -> np.ndarray | None:
    """Convenience for callers that need a single embedding (e.g.
    rationale's retrieval step)."""
    return embedder_for("retrieval").embed(query)
