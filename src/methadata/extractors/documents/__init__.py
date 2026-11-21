from .extractor import EXTRACTOR_VERSION, DerivedStore, DocumentsExtractor

ADAPTERS = [DocumentsExtractor()]


def get_adapters():
    return [DocumentsExtractor()]


__all__ = ["DocumentsExtractor", "DerivedStore", "EXTRACTOR_VERSION", "ADAPTERS", "get_adapters"]
