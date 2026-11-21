from .extractor import EXTRACTOR_VERSION, DerivedStore, HtmlExtractor

ADAPTERS = [HtmlExtractor()]


def get_adapters():
    return [HtmlExtractor()]


__all__ = ["HtmlExtractor", "DerivedStore", "EXTRACTOR_VERSION", "ADAPTERS", "get_adapters"]
