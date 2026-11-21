from .adapter import (ADAPTERS_VERSION, AffiliateComparator, AffiliateExtractor, parse_affiliate_url, parse_text_codes)


def get_adapters():
    return [AffiliateExtractor(), AffiliateComparator()]


ADAPTERS = get_adapters()

__all__ = ["AffiliateExtractor", "AffiliateComparator", "parse_affiliate_url", "parse_text_codes", "get_adapters",
           "ADAPTERS", "ADAPTERS_VERSION"]
