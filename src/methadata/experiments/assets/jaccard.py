from __future__ import annotations

from typing import Collection, Mapping, Optional


def jaccard(a: Collection[str], b: Collection[str]) -> Optional[float]:
    """|A∩B| / |A∪B|; None (brak podstaw), gdy oba zbiory są puste."""
    a, b = set(a), set(b)
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def weighted_jaccard(a: Collection[str], b: Collection[str], freq: Mapping[str, int]) -> Optional[float]:
    """Σw(A∩B) / Σw(A∪B), w(h) = 1 / max(1, freq[h]); freq = liczba materiałów korpusu zawierających hash."""
    a, b = set(a), set(b)
    union = a | b
    if not union:
        return None
    w = lambda h: 1.0 / max(1, freq.get(h, 1))
    return sum(w(h) for h in a & b) / sum(w(h) for h in union)
