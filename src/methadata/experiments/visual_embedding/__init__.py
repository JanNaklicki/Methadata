"""E11: embeddingi wizualne i multimodalne (wektory ilustracji i wycinków, cosinusy, wariant podpis-obraz)."""
from .adapters import (EXTRACTOR_VERSION, CaptionCrossModalComparator, VisualEmbeddingComparator,
                       VisualEmbeddingExtractor, get_adapters)
from .backends import Backend, BackendUnavailable, ColorLayoutBackend, OpenClipBackend, make_backend
from .config import DEFAULTS, load_config

__all__ = ["VisualEmbeddingExtractor", "VisualEmbeddingComparator", "CaptionCrossModalComparator", "get_adapters",
           "EXTRACTOR_VERSION", "DEFAULTS", "load_config", "Backend", "BackendUnavailable", "ColorLayoutBackend",
           "OpenClipBackend", "make_backend"]
