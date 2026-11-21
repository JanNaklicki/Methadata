"""Pomiar kandydatów MinHash/LSH względem wszystkich par: recall (pary o dokładnym Jaccard >= próg) i czas."""
from __future__ import annotations

import random
import time
from itertools import combinations
from typing import Mapping, Optional

from .shingle import DEFAULT_K, MinHasher, jaccard, lsh_candidates, shingles


def synthetic_corpus(n_families: int = 40, variants: int = 8, words_per_doc: int = 400, edit_rate: float = 0.08,
                     seed: int = 1) -> tuple[dict[str, str], dict[str, str]]:
    """Rodziny wariantów (kopia z losowymi podmianami słów) + teksty niezwiązane. -> (teksty, rodzina)."""
    rnd = random.Random(seed)
    vocab = [f"w{i}" for i in range(6000)]
    docs, fam = {}, {}
    for f in range(n_families):
        base = [rnd.choice(vocab) for _ in range(words_per_doc)]
        for v in range(variants):
            t = [rnd.choice(vocab) if rnd.random() < edit_rate else w for w in base]
            docs[f"f{f}v{v}"] = " ".join(t)
            fam[f"f{f}v{v}"] = f"f{f}"
    return docs, fam


def measure(docs: Mapping[str, str], *, k: int = DEFAULT_K, num_perm: int = 128, bands: int = 32,
            threshold: float = 0.3, seed: int = 0) -> dict:
    t0 = time.perf_counter()
    sh = {key: shingles(t, k) for key, t in docs.items()}
    t_sh = time.perf_counter() - t0
    t0 = time.perf_counter()
    exact = {}
    for a, b in combinations(sorted(sh), 2):
        j = jaccard(sh[a], sh[b])
        if j is not None:
            exact[(a, b)] = j
    t_all = time.perf_counter() - t0
    mh = MinHasher(num_perm, seed)
    t0 = time.perf_counter()
    sigs = {key: mh.signature(s) for key, s in sh.items()}
    cand = lsh_candidates(sigs, bands)
    t_lsh = time.perf_counter() - t0
    t0 = time.perf_counter()
    for a, b in cand:
        jaccard(sh[a], sh[b])
    t_ver = time.perf_counter() - t0
    true = {p for p, j in exact.items() if j >= threshold}
    recall: Optional[float] = len(true & cand) / len(true) if true else None
    n = len(sh)
    return {"docs": n, "pairs_all": n * (n - 1) // 2, "pairs_true": len(true), "candidates": len(cand),
            "recall": recall, "t_shingles_s": t_sh, "t_all_pairs_s": t_all, "t_lsh_s": t_lsh,
            "t_verify_candidates_s": t_ver, "config": {"k": k, "num_perm": num_perm, "bands": bands,
                                                       "threshold": threshold, "seed": seed}}


if __name__ == "__main__":
    import json
    import sys
    fam = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    docs, _ = synthetic_corpus(n_families=fam)
    print(json.dumps(measure(docs, bands=int(sys.argv[2]) if len(sys.argv) > 2 else 32), indent=1))
