"""Embeddings + retrieval for uploaded documents.

Pipeline:

    document.sections                          (from extractor)
        →   chunker.chunk(section, ~500 tokens, ~100 overlap)
        →   embedder.embed(chunk_text)         (Ollama, default
                                                mxbai-embed-large)
        →   store.add(doc_id, chunk, embedding)
                                            (Parquet on the Mac)

At decision time (rationale.gather_facts):

        store.retrieve(symbol, query, k=5)
        →   list of {doc_id, chunk_id, heading, page, text} chunks
            with stable URIs `tradepro://documents/<doc_id>#chunk-<n>`

The retrieved chunks become additional `allowed facts` for the LLM
rationale — verifier still gates the output, so a citation that
doesn't trace to a chunk URI is rejected. Same no-hallucination
contract as the structured row data.

Mac is source of truth for embeddings. The .NET API keeps the
document text; the Mac keeps the vector index. Browser uploads land
in the API → comparator's `update()` catches up the embedding store
on its next run.
"""
from .chunker import Chunk, chunk_document
from .embedder import OllamaEmbedder, embedder_for, embed_query
from .store import EmbeddingStore, RetrievedChunk, default_store
from .updater import update_embeddings

__all__ = [
    "Chunk",
    "chunk_document",
    "OllamaEmbedder",
    "embedder_for",
    "embed_query",
    "EmbeddingStore",
    "RetrievedChunk",
    "default_store",
    "update_embeddings",
]
