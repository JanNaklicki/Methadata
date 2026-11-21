from .adapter import (ADAPTERS_VERSION, MobileAppComparator, MobileAppExtractor, parse_assetlinks, parse_store_url)


def get_adapters():
    return [MobileAppExtractor(), MobileAppComparator()]


ADAPTERS = get_adapters()

__all__ = ["MobileAppExtractor", "MobileAppComparator", "parse_store_url", "parse_assetlinks", "get_adapters",
           "ADAPTERS", "ADAPTERS_VERSION"]
