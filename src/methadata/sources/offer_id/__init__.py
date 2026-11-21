from .adapter import ADAPTERS_VERSION, OfferIdComparator, OfferIdExtractor
from .gtin import check_digit_ok, normalize_gtin


def get_adapters():
    return [OfferIdExtractor(), OfferIdComparator()]


ADAPTERS = get_adapters()

__all__ = ["OfferIdExtractor", "OfferIdComparator", "normalize_gtin", "check_digit_ok", "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
