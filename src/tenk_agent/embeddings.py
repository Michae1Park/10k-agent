"""Text embeddings and reranking behind small interfaces, so models can be swapped per eval run.

Specs look like "local:BAAI/bge-base-en-v1.5" or "voyage:voyage-3.5" (see config.py).
Local models need the optional `local` extra (sentence-transformers).
"""

import logging
import os
from functools import cache
from typing import Protocol

import httpx
import numpy as np

log = logging.getLogger(__name__)

# bge models retrieve better when queries (not documents) carry this instruction.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    name: str

    def scores(self, query: str, texts: list[str]) -> list[float]: ...


class LocalEmbedder:
    def __init__(self, model: str, device: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.name = f"local:{model}"
        self._model = SentenceTransformer(model, device=device)
        self._query_prefix = BGE_QUERY_PREFIX if "bge" in model.lower() else ""
        # Models that ship a named query prompt (e.g. Qwen3-Embedding) use it instead.
        self._prompt_name = "query" if "query" in (self._model.prompts or {}) else None

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=len(texts) > 500
        )

    def embed_query(self, text: str) -> np.ndarray:
        if self._prompt_name:
            return self._model.encode(
                [text], prompt_name=self._prompt_name, normalize_embeddings=True,
                show_progress_bar=False,
            )[0]  # fmt: skip
        return self._model.encode(
            [self._query_prefix + text], normalize_embeddings=True, show_progress_bar=False
        )[0]


class VoyageEmbedder:
    URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, model: str):
        self.name = f"voyage:{model}"
        self._model = model
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"}, timeout=120
        )

    def _embed(self, texts: list[str], input_type: str) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), 64):
            response = self._http.post(
                self.URL,
                json={
                    "input": texts[start : start + 64],
                    "model": self._model,
                    "input_type": input_type,
                },
            )
            response.raise_for_status()
            vectors += [d["embedding"] for d in response.json()["data"]]
        array = np.array(vectors, dtype=np.float32)
        return array / np.linalg.norm(array, axis=1, keepdims=True)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "query")[0]


class LocalReranker:
    def __init__(self, model: str, device: str | None = None):
        from sentence_transformers import CrossEncoder

        self.name = f"local:{model}"
        self._model = CrossEncoder(model, max_length=512, device=device)

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [
            float(s)
            for s in self._model.predict([(query, t) for t in texts], show_progress_bar=False)
        ]


class HostedReranker:
    """Cohere and Voyage share the same request/response shape for reranking."""

    URLS = {
        "cohere": ("https://api.cohere.com/v2/rerank", "COHERE_API_KEY"),
        "voyage": ("https://api.voyageai.com/v1/rerank", "VOYAGE_API_KEY"),
    }

    def __init__(self, provider: str, model: str):
        self.name = f"{provider}:{model}"
        self._url, key_var = self.URLS[provider]
        self._provider = provider
        self._model = model
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {os.environ[key_var]}"}, timeout=60
        )

    def scores(self, query: str, texts: list[str]) -> list[float]:
        response = self._http.post(
            self._url, json={"model": self._model, "query": query, "documents": texts}
        )
        response.raise_for_status()
        results = response.json()["results" if self._provider == "cohere" else "data"]
        scores = [0.0] * len(texts)
        for result in results:
            scores[result["index"]] = result["relevance_score"]
        return scores


@cache
def get_embedder(spec: str, device: str | None = None) -> Embedder:
    """`device` (e.g. "cpu") keeps local models off a GPU that vLLM fills."""
    provider, _, model = spec.partition(":")
    if provider == "local":
        return LocalEmbedder(model, device)
    if provider == "voyage":
        return VoyageEmbedder(model)
    raise ValueError(f"Unknown embedder {spec!r}")


@cache
def get_reranker(spec: str, device: str | None = None) -> Reranker | None:
    provider, _, model = spec.partition(":")
    if provider in ("", "none"):
        return None
    if provider == "local":
        return LocalReranker(model, device)
    if provider in HostedReranker.URLS:
        return HostedReranker(provider, model)
    raise ValueError(f"Unknown reranker {spec!r}")
