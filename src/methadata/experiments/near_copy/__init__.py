"""E05: bliskie kopie tekstu (Jaccard shingles, zawieranie) i MinHash/LSH do wyszukiwania kandydatów."""
from .adapters import NearCopyComparator, get_adapters
from .shingle import (DEFAULT_K, MinHasher, containment, jaccard, lsh_candidates, rank_approx, rank_exact,
                      shingles)

__all__ = ["NearCopyComparator", "get_adapters", "DEFAULT_K", "MinHasher", "containment", "jaccard",
           "lsh_candidates", "rank_approx", "rank_exact", "shingles"]
