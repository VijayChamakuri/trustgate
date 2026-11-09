"""Local text embeddings (free, CPU, ONNX via fastembed) with an on-disk cache.

No API calls and no GPU: `BAAI/bge-small-en-v1.5` (33M params, 384-d) downloads once from
Hugging Face (~130 MB) and runs on a laptop CPU. Vectors are cached by text hash so reruns
are instant and results are reproducible.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .paths import DATA

MODEL = "BAAI/bge-small-en-v1.5"
CACHE = DATA / "embeddings"


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


class Embedder:
    def __init__(self, model: str = MODEL, cache_dir: Path = CACHE):
        self.model_name = model
        self.cache_file = cache_dir / f"{model.replace('/', '__')}.npz"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, np.ndarray] = {}
        if self.cache_file.exists():
            z = np.load(self.cache_file)
            self._cache = dict(zip(z["keys"].tolist(), z["vecs"], strict=True))
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name)
        return self._model

    def encode(self, texts: list[str], batch_size: int = 256) -> np.ndarray:
        keys = [_key(t) for t in texts]
        missing = sorted({k: t for k, t in zip(keys, texts, strict=True) if k not in self._cache}.items())
        if missing:
            vecs = list(self._load().embed([t for _, t in missing], batch_size=batch_size))
            for (k, _), v in zip(missing, vecs, strict=True):
                self._cache[k] = np.asarray(v, dtype=np.float32)
            np.savez(self.cache_file, keys=np.array(list(self._cache)), vecs=np.stack(list(self._cache.values())))
        out = np.stack([self._cache[k] for k in keys])
        return out / np.linalg.norm(out, axis=1, keepdims=True).clip(min=1e-9)
