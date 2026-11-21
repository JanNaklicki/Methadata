"""Wymienne backendy embeddingów akapitów + cache według treści.

Backend rzeczywisty (sentence-transformers) importowany leniwie; brak biblioteki lub wag daje jawny status
`unsupported` (BackendUnavailable), a nie ciche przybliżenie."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_REVISION = "unpinned"  # przy pierwszym uruchomieniu zapisz commit z huggingface (patrz config)

Vector = list[float]


class BackendUnavailable(RuntimeError):
    """Brak biblioteki, wag lub pobrania: wynik porównania ma status unsupported."""


class EmbeddingBackend(Protocol):
    model_id: str
    revision: str

    def encode(self, texts: Sequence[str]) -> list[Vector]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0 or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class SentenceTransformerBackend:
    """paraphrase-multilingual-MiniLM (opcjonalny extra `semantic`); wagi w cache poza repo (cache_dir)."""

    def __init__(self, model_id: str = DEFAULT_MODEL, revision: str = DEFAULT_REVISION,
                 cache_dir: Optional[str] = None, local_only: bool = False):
        self.model_id, self.revision, self.cache_dir, self.local_only = model_id, revision, cache_dir, local_only
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # lazy
            except Exception as e:
                raise BackendUnavailable(f"brak biblioteki sentence-transformers ({type(e).__name__})") from e
            try:
                kw = {"cache_folder": self.cache_dir} if self.cache_dir else {}
                if self.revision != "unpinned":
                    kw["revision"] = self.revision
                if self.local_only:
                    kw["local_files_only"] = True
                self._model = SentenceTransformer(self.model_id, **kw)
            except Exception as e:
                raise BackendUnavailable(f"brak lokalnych wag modelu {self.model_id} ({type(e).__name__})") from e
        return self._model

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        m = self._load()
        return [[float(x) for x in v] for v in m.encode(list(texts), show_progress_bar=False)]


class CallableBackend:
    """Backend z funkcji (testy na wektorach syntetycznych, ONNX itp.)."""

    def __init__(self, fn: Callable[[Sequence[str]], list[Vector]], model_id: str = "callable", revision: str = "1"):
        self.fn, self.model_id, self.revision = fn, model_id, revision

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        return self.fn(texts)


class EmbeddingCache:
    """Cache wektorów według treści: klucz = sha256(model|rewizja|tekst). Pamięć + opcjonalny katalog JSON."""

    def __init__(self, backend: EmbeddingBackend, directory: Optional[str | Path] = None):
        self.backend = backend
        self.dir = Path(directory) if directory else None
        self.mem: dict[str, Vector] = {}
        self.hits = self.misses = 0

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.backend.model_id}\0{self.backend.revision}\0{text}".encode()).hexdigest()

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        keys = [self.key(t) for t in texts]
        todo: dict[str, str] = {}
        for k, t in zip(keys, texts):
            if k in self.mem:
                self.hits += 1
                continue
            p = self.dir / f"{k}.json" if self.dir else None
            if p is not None and p.is_file():
                self.mem[k] = json.loads(p.read_text())
                self.hits += 1
            else:
                todo.setdefault(k, t)
        if todo:
            self.misses += len(todo)
            vecs = self.backend.encode(list(todo.values()))
            for k, v in zip(todo, vecs):
                self.mem[k] = list(v)
                if self.dir:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    (self.dir / f"{k}.json").write_text(json.dumps(v))
        return [self.mem[k] for k in keys]
