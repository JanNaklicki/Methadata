from .extractor import EXTRACTOR_VERSION, CodeExtractor, DerivedStore, classify
from .jsscan import similarity as js_similarity

ADAPTERS = [CodeExtractor()]


def get_adapters():
    return [CodeExtractor()]


__all__ = ["CodeExtractor", "DerivedStore", "EXTRACTOR_VERSION", "ADAPTERS", "get_adapters", "classify", "js_similarity"]
