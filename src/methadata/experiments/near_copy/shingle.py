"""Shingles (k kolejnych słów), Jaccard, zawieranie oraz MinHash/LSH (czysty Python, deterministyczne)."""
from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from itertools import combinations
from typing import Iterable, Mapping, Optional, Sequence

from methadata.experiments.lexical.tfidf import words

DEFAULT_K = 5
_MASK = (1 << 61) - 1  # liczba pierwsza Mersenne'a 2^61-1
_P = _MASK


def shingles(text: str, k: int = DEFAULT_K) -> set[tuple[str, ...]]:
    """Zbiór k-krotek słów. Tekst krótszy niż k słów daje jedną krotkę (wszystkie słowa); pusty -> zbiór pusty."""
    w = words(text)
    if not w:
        return set()
    if len(w) < k:
        return {tuple(w)}
    return {tuple(w[i:i + k]) for i in range(len(w) - k + 1)}


def jaccard(a: set, b: set) -> Optional[float]:
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def containment(a: set, b: set) -> Optional[float]:
    """Udział krótszego zbioru zawarty w dłuższym: |A∩B| / min(|A|,|B|)."""
    if not a or not b:
        return None
    return len(a & b) / min(len(a), len(b))


def shingle_hash(sh: tuple[str, ...]) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(sh).encode(), digest_size=8).digest(), "big") & _MASK


class MinHasher:
    """Sygnatura MinHash o `num_perm` permutacjach h(x)=(a x+b) mod p; ziarno stałe -> wynik powtarzalny."""

    def __init__(self, num_perm: int = 128, seed: int = 0):
        rnd = random.Random(seed)
        self.num_perm = num_perm
        self.seed = seed
        self.ab = [(rnd.randrange(1, _P), rnd.randrange(0, _P)) for _ in range(num_perm)]

    def signature(self, sh: Iterable[tuple[str, ...]]) -> Optional[tuple[int, ...]]:
        hs = [shingle_hash(s) for s in sh]
        if not hs:
            return None
        return tuple(min((a * h + b) % _P for h in hs) for a, b in self.ab)

    @staticmethod
    def estimate(s1: Sequence[int], s2: Sequence[int]) -> float:
        return sum(x == y for x, y in zip(s1, s2)) / len(s1)


def lsh_candidates(signatures: Mapping[str, Optional[Sequence[int]]], bands: int = 32) -> set[tuple[str, str]]:
    """LSH pasmowy: pary z identycznym fragmentem sygnatury w co najmniej jednym paśmie.
    Próg ~ (1/bands)^(1/rows), rows = num_perm // bands. Klucze par uporządkowane rosnąco."""
    buckets: dict[tuple[int, tuple[int, ...]], list[str]] = defaultdict(list)
    for key, sig in signatures.items():
        if not sig:
            continue
        rows = len(sig) // bands
        for b in range(bands):
            buckets[(b, tuple(sig[b * rows:(b + 1) * rows]))].append(key)
    out: set[tuple[str, str]] = set()
    for members in buckets.values():
        for x, y in combinations(sorted(set(members)), 2):
            out.add((x, y))
    return out


def rank_exact(query: set, corpus: Mapping[str, set], measure=jaccard) -> list[tuple[str, float]]:
    r = [(k, measure(query, v)) for k, v in corpus.items()]
    return sorted(((k, s) for k, s in r if s is not None), key=lambda t: (-t[1], t[0]))


def rank_approx(query_sig: Sequence[int], sigs: Mapping[str, Optional[Sequence[int]]]) -> list[tuple[str, float]]:
    r = [(k, MinHasher.estimate(query_sig, s)) for k, s in sigs.items() if s]
    return sorted(r, key=lambda t: (-t[1], t[0]))
