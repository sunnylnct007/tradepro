"""Parquet-backed vector store + numpy brute-force retrieval.

Scale assumption: single-user, dozens of documents, hundreds of
chunks. Brute-force cosine similarity in numpy is plenty — no need
for FAISS / DuckDB-VSS / sqlite-vec at this size, and it lets the
store be a single Parquet file the user can inspect, copy, or delete.

Storage layout (one row per chunk):
    chunk_id   str    stable hash
    doc_id     str
    symbols    list[str]
    heading    str | null
    page       int | null
    section_index   int
    chunk_in_section int
    text       str    (full chunk body — used for citation)
    model      str    embedding model name
    embedding  list[float]   variable-dim (1024 for mxbai-embed-large)

File path: ~/.tradepro/cache/embeddings.parquet

Concurrency: this is a single-Mac asset. The updater uses an exclusive
write lock around the load-modify-save cycle. Readers hit a copy
loaded into memory, so they don't block.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .chunker import Chunk

DEFAULT_PATH = Path.home() / ".tradepro" / "cache" / "embeddings.parquet"


@dataclass
class RetrievedChunk:
    """One retrieval result — what the rationale's facts bundle gets."""
    chunk_id: str
    doc_id: str
    heading: str | None
    page: int | None
    text: str
    score: float
    uri: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "heading": self.heading,
            "page": self.page,
            "text": self.text,
            "score": self.score,
            "uri": self.uri,
        }


class EmbeddingStore:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._path = Path(path)
        self._df: pd.DataFrame | None = None

    # ---- I/O -------------------------------------------------------------

    def _load(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        if not self._path.exists():
            self._df = self._empty_frame()
            return self._df
        try:
            self._df = pd.read_parquet(self._path)
            # Embedding column comes back as a list of arrays; ensure
            # numpy-native for fast dot-product.
            if "embedding" in self._df.columns and len(self._df):
                self._df["embedding"] = self._df["embedding"].apply(
                    lambda x: np.asarray(x, dtype=np.float32)
                )
        except Exception:
            # Corrupt file → start fresh; the operator can `rm
            # ~/.tradepro/cache/embeddings.parquet` to force a rebuild.
            self._df = self._empty_frame()
        return self._df

    def _save(self) -> None:
        if self._df is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Convert numpy arrays to lists for Parquet round-trip.
        out = self._df.copy()
        if "embedding" in out.columns and len(out):
            out["embedding"] = out["embedding"].apply(
                lambda x: x.tolist() if isinstance(x, np.ndarray) else list(x or [])
            )
        out.to_parquet(self._path, index=False)

    @staticmethod
    def _empty_frame() -> pd.DataFrame:
        return pd.DataFrame({
            "chunk_id": pd.Series(dtype="str"),
            "doc_id": pd.Series(dtype="str"),
            "symbols": pd.Series(dtype="object"),
            "heading": pd.Series(dtype="object"),
            "page": pd.Series(dtype="object"),
            "section_index": pd.Series(dtype="Int64"),
            "chunk_in_section": pd.Series(dtype="Int64"),
            "text": pd.Series(dtype="str"),
            "model": pd.Series(dtype="str"),
            "embedding": pd.Series(dtype="object"),
        })

    # ---- Mutation --------------------------------------------------------

    def has_chunk(self, chunk_id: str, model: str) -> bool:
        df = self._load()
        if df.empty:
            return False
        m = (df["chunk_id"] == chunk_id) & (df["model"] == model)
        return bool(m.any())

    def add(
        self,
        chunk: Chunk,
        embedding: np.ndarray,
        model: str,
    ) -> None:
        """Insert (or replace) a single chunk's embedding for the given
        model. Idempotent — re-running with the same chunk + model is a
        no-op rather than an error."""
        df = self._load()
        # Drop any prior row for this (chunk_id, model).
        if not df.empty:
            mask = ~((df["chunk_id"] == chunk.chunk_id) & (df["model"] == model))
            df = df.loc[mask].reset_index(drop=True)

        new_row = {
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "symbols": list(chunk.symbols),
            "heading": chunk.heading,
            "page": chunk.page,
            "section_index": chunk.section_index,
            "chunk_in_section": chunk.chunk_in_section,
            "text": chunk.text,
            "model": model,
            "embedding": np.asarray(embedding, dtype=np.float32),
        }
        self._df = pd.concat(
            [df, pd.DataFrame([new_row])], ignore_index=True,
        )

    def remove_doc(self, doc_id: str) -> int:
        """Delete every chunk for a given document. Returns the number
        of rows dropped."""
        df = self._load()
        if df.empty:
            return 0
        before = len(df)
        self._df = df.loc[df["doc_id"] != doc_id].reset_index(drop=True)
        return before - len(self._df)

    def commit(self) -> None:
        self._save()

    # ---- Read ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._load())

    def doc_chunk_ids(self, doc_id: str, model: str) -> set[str]:
        df = self._load()
        if df.empty:
            return set()
        m = (df["doc_id"] == doc_id) & (df["model"] == model)
        return set(df.loc[m, "chunk_id"].tolist())

    def retrieve(
        self,
        query_vec: np.ndarray,
        symbols: Iterable[str],
        k: int = 5,
        model: str | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedChunk]:
        """Top-K chunks by cosine similarity, filtered to chunks tagged
        with at least one of the given symbols. `min_score` lets the
        caller drop weak matches — for short prompts we typically want
        score > 0.3 or so, but 0.0 is fine for a discovery search."""
        df = self._load()
        if df.empty:
            return []

        wanted = {s.upper() for s in symbols if s}
        if wanted:
            mask = df["symbols"].apply(
                lambda lst: bool(wanted & {s.upper() for s in (lst or [])})
            )
            df = df.loc[mask]
        if model:
            df = df.loc[df["model"] == model]
        if df.empty:
            return []

        # Cosine similarity. Embeddings aren't always L2-normalised;
        # compute via dot / (||a|| * ||b||).
        q = np.asarray(query_vec, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []
        scores: list[float] = []
        for v in df["embedding"]:
            v = np.asarray(v, dtype=np.float32)
            v_norm = float(np.linalg.norm(v))
            if v_norm == 0.0:
                scores.append(0.0)
            else:
                scores.append(float(np.dot(q, v) / (q_norm * v_norm)))
        df = df.assign(_score=scores)
        df = df.sort_values("_score", ascending=False)
        df = df.loc[df["_score"] >= min_score].head(k)

        out: list[RetrievedChunk] = []
        for _, row in df.iterrows():
            out.append(RetrievedChunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                heading=row["heading"],
                page=int(row["page"]) if pd.notna(row["page"]) else None,
                text=row["text"],
                score=float(row["_score"]),
                uri=f"tradepro://documents/{row['doc_id']}#chunk-{row['chunk_id']}",
            ))
        return out


def default_store() -> EmbeddingStore:
    return EmbeddingStore()
