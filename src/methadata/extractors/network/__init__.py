from .extractor import EXTRACTOR_VERSION, FAMILY, NetworkExtractor  # noqa: F401
from .store import DerivedStore, MemoryDerivedStore  # noqa: F401


def get_adapters():
    return [NetworkExtractor()]
