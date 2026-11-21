from .adapter import ADAPTERS_VERSION, QrComparator, QrExtractor
from .normalize import classify, normalize_url


def get_adapters():
    return [QrExtractor(), QrComparator()]


ADAPTERS = get_adapters()

__all__ = ["QrExtractor", "QrComparator", "classify", "normalize_url", "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
