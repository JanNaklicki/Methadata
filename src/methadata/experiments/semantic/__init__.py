"""E06: wielojęzyczne embeddingi akapitów, cosinus i najlepsze dopasowania; osobno tytuł, obietnica (body) i CTA."""
from .backend import (BackendUnavailable, CallableBackend, EmbeddingCache, SentenceTransformerBackend, cosine)
from .compare import SemanticParagraphComparator, compare_secs, get_adapters, match_field

__all__ = ["BackendUnavailable", "CallableBackend", "EmbeddingCache", "SentenceTransformerBackend", "cosine",
           "SemanticParagraphComparator", "compare_secs", "get_adapters", "match_field"]
