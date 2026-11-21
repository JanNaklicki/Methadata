"""Regresja logistyczna (L2) w czystym Pythonie: cechy = jawne podobieństwa + wskaźniki braków."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

MISS = "__missing__"


def feature_names(methods: Sequence[str]) -> list[str]:
    return [f"sim:{m}" for m in methods] + [f"{MISS}:{m}" for m in methods]


def featurize(scores: Mapping[str, Optional[float]], methods: Sequence[str]) -> list[float]:
    """Wynik None/brak metody -> podobieństwo 0 i wskaźnik braku 1 (brak podstaw to nie „niepodobne”)."""
    sims, miss = [], []
    for m in methods:
        s = scores.get(m)
        sims.append(0.0 if s is None else float(s))
        miss.append(1.0 if s is None else 0.0)
    return sims + miss


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


@dataclass
class Logit:
    names: list[str]
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    l2: float = 0.1

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int], *, epochs: int = 800, lr: float = 0.5) -> "Logit":
        if set(y) != {0, 1}:
            raise ValueError("uczenie wymaga ocen obu klas (tak i nie)")
        n, d = len(X), len(self.names)
        w, b = [0.0] * d, 0.0
        for _ in range(epochs):
            gw, gb = [0.0] * d, 0.0
            for xi, yi in zip(X, y):
                e = _sig(sum(a * c for a, c in zip(w, xi)) + b) - yi
                gb += e
                for j in range(d):
                    gw[j] += e * xi[j]
            w = [wj - lr * (g / n + self.l2 * wj) for wj, g in zip(w, gw)]
            b -= lr * gb / n
        self.weights, self.bias = w, b
        return self

    def proba(self, x: Sequence[float]) -> float:
        return _sig(sum(a * c for a, c in zip(self.weights, x)) + self.bias)

    def contributions(self, x: Sequence[float]) -> list[tuple[str, float]]:
        """Wkład cechy do logitu (waga*wartość), od największego co do modułu; zerowe pomijane."""
        c = [(n, w * v) for n, w, v in zip(self.names, self.weights, x) if w * v != 0.0]
        return sorted(c, key=lambda t: (-abs(t[1]), t[0]))

    def to_json(self) -> dict:
        return {"names": self.names, "weights": self.weights, "bias": self.bias, "l2": self.l2}

    @classmethod
    def from_json(cls, d: Mapping) -> "Logit":
        return cls(names=list(d["names"]), weights=list(d["weights"]), bias=d["bias"], l2=d.get("l2", 0.1))


def auc(y: Sequence[int], p: Sequence[float]) -> Optional[float]:
    pos = [s for s, t in zip(p, y) if t == 1]
    neg = [s for s, t in zip(p, y) if t == 0]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))
