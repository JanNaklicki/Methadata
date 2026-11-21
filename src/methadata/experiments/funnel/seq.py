"""Porównanie kolejności etapów: odległość edycyjna i najdłuższy wspólny podciąg (ciągły) / podciąg."""
from __future__ import annotations

from typing import Optional, Sequence

from methadata.experiments.dom.seq import levenshtein


def edit_similarity(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    if not a or not b:
        return None
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def lcs_pairs(a: Sequence[str], b: Sequence[str]) -> list[tuple[int, int]]:
    """Najdłuższy wspólny podciąg (niekoniecznie ciągły): pary indeksów (i, j); wynik deterministyczny."""
    n, m = len(a), len(b)
    t = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            t[i][j] = t[i + 1][j + 1] + 1 if a[i] == b[j] else max(t[i + 1][j], t[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.append((i, j))
            i += 1
            j += 1
        elif t[i + 1][j] >= t[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def lcs_similarity(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    if not a or not b:
        return None
    return len(lcs_pairs(a, b)) / max(len(a), len(b))
