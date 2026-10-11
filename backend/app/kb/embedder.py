"""Text embedders. The tenant schema stores 384-dimensional vectors (the all-MiniLM-L6-v2 size).

* FastEmbedder  - real semantic embeddings via ONNX (no PyTorch). The model is downloaded once and
                  loaded lazily; inference runs in a worker thread so the event loop is never blocked.
* HashingEmbedder - deterministic bag-of-words hashing. Needs no download; used for tests and as an
                  offline development fallback (CRM_EMBEDDER=hash). Similar wording gives similar vectors,
                  but it has no real semantic understanding.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from typing import List, Protocol

import numpy as np

DIMENSIONS = 384
FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder(Protocol):
    name: str

    async def embed(self, texts: List[str]) -> List[np.ndarray]: ...


def _normalise(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return (vector / norm).astype(np.float32) if norm > 0 else vector.astype(np.float32)


class HashingEmbedder:
    name = "hash"
    _TOKEN = re.compile(r"[a-z0-9]+")

    def _one(self, text: str) -> np.ndarray:
        vector = np.zeros(DIMENSIONS, dtype=np.float32)
        tokens = self._TOKEN.findall(text.lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % DIMENSIONS
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        # Bigrams give a little word-order signal.
        for first, second in zip(tokens, tokens[1:]):
            digest = hashlib.blake2b(f"{first} {second}".encode("utf-8"), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "little") % DIMENSIONS] += 0.5 if digest[4] & 1 else -0.5
        return _normalise(vector)

    async def embed(self, texts: List[str]) -> List[np.ndarray]:
        return [self._one(t) for t in texts]


class FastEmbedder:
    name = "fastembed"

    def __init__(self, model_name: str = FASTEMBED_MODEL) -> None:
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(model_name=self._model_name)
            return self._model

    def _run(self, texts: List[str]) -> List[np.ndarray]:
        model = self._load()
        return [_normalise(np.asarray(v, dtype=np.float32)) for v in model.embed(texts)]

    async def embed(self, texts: List[str]) -> List[np.ndarray]:
        if not texts:
            return []
        return await asyncio.to_thread(self._run, texts)


def build_embedder(kind: str) -> Embedder:
    return HashingEmbedder() if kind == "hash" else FastEmbedder()
