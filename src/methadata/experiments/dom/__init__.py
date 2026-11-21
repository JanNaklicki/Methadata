"""E03: podobieństwo struktury DOM (ścieżki tagów, kolejność sekcji, odległość edycyjna sekwencji)."""
from .adapters import (EXTRACTOR_VERSION, DomStructureExtractor, PathSetComparator, SectionOrderComparator,
                       SequenceEditComparator, get_adapters)
from .seq import common_blocks, edit_similarity, levenshtein, tag_paths

__all__ = ["DomStructureExtractor", "PathSetComparator", "SequenceEditComparator", "SectionOrderComparator",
           "get_adapters", "EXTRACTOR_VERSION", "levenshtein", "edit_similarity", "common_blocks", "tag_paths"]
